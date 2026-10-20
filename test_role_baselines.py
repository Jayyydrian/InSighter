import os
import unittest
from contextlib import closing
from unittest.mock import patch

import numpy as np
import pandas as pd

import database
import model
import role_baseline
from test_support import isolate_database, temporary_database_path

isolate_database()
os.environ.setdefault("INSIGHTER_DISABLE_BACKGROUND_SIM", "1")

from app import app
from auth import init_users_table
from generate_logs import generate
from sector_config import SECTORS


class RoleBaselineStorageAndScoringTest(unittest.TestCase):
    def setUp(self):
        self.database_path = temporary_database_path()
        self.previous_database_path = database.DB_PATH
        database.DB_PATH = self.database_path
        database.ensure_database()
        init_users_table()
        self.set_sector("private_school")

    def tearDown(self):
        database.DB_PATH = self.previous_database_path
        if os.path.exists(self.database_path):
            os.remove(self.database_path)

    def set_sector(self, sector):
        with closing(database.connect()) as conn:
            conn.execute(
                "UPDATE deployment_config SET sector = ? WHERE id = 1",
                (sector,),
            )
            conn.commit()

    def test_migration_is_idempotent_and_preserves_existing_rows(self):
        with closing(database.connect()) as conn:
            conn.execute("CREATE TABLE logs (user TEXT, marker TEXT)")
            conn.execute("INSERT INTO logs VALUES ('alice', 'preserve-me')")
            conn.execute("CREATE TABLE customer_data (id INTEGER PRIMARY KEY, value TEXT)")
            conn.execute("INSERT INTO customer_data VALUES (1, 'keep')")
            conn.commit()

        database.ensure_database()
        database.ensure_database()

        with closing(database.connect()) as conn:
            self.assertEqual(
                conn.execute("SELECT user, marker FROM logs").fetchall(),
                [("alice", "preserve-me")],
            )
            self.assertEqual(
                conn.execute("SELECT value FROM customer_data").fetchone()[0],
                "keep",
            )
            names = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            self.assertTrue({"role_baselines", "baseline_setup_status"} <= names)

    def test_feature_units_and_data_grounded_bounds(self):
        expected = {
            "files_accessed": ("files/event", 0, 155),
            "data_transferred_mb": ("MB/event", 0, 1202),
            "failed_logins": ("attempts/event", 0, 13.5),
            "off_hours_access": ("fraction of events", 0, 1),
        }
        for feature, (unit, minimum, maximum) in expected.items():
            with self.subTest(feature=feature):
                spec = role_baseline.ROLE_BASELINE_FEATURES[feature]
                self.assertEqual(spec["unit"], unit)
                self.assertEqual(spec["min"], minimum)
                self.assertEqual(spec["max"], maximum)

    def test_sector_isolation_values_survive_reseed_and_reset(self):
        legacy_sector = "legacy_sector"
        with closing(database.connect()) as conn:
            conn.execute(
                "INSERT INTO role_baselines (sector, role, feature, value) VALUES "
                "(?, 'researcher', 'files_accessed', 7), "
                "('private_school', 'researcher', 'files_accessed', 9)",
                (legacy_sector,),
            )
            conn.execute(
                "INSERT INTO baseline_setup_status (sector, completed) VALUES "
                "(?, 1), ('private_school', 1)",
                (legacy_sector,),
            )
            conn.commit()

        self.assertEqual(
            role_baseline.get_declared_role_baselines(legacy_sector)["researcher"]["files_accessed"],
            7,
        )
        self.assertEqual(
            role_baseline.get_declared_role_baselines("private_school")["researcher"]["files_accessed"],
            9,
        )

        generate()

        with closing(database.connect()) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT value FROM role_baselines WHERE sector=? AND role='researcher'",
                    (legacy_sector,),
                ).fetchone()[0],
                7,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT completed FROM baseline_setup_status WHERE sector=?",
                    (legacy_sector,),
                ).fetchone()[0],
                1,
            )

        response = self.admin_client().post("/api/baseline-setup/reset")
        self.assertEqual(response.status_code, 200)
        with closing(database.connect()) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM role_baselines WHERE sector=?",
                    (legacy_sector,),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT completed FROM baseline_setup_status WHERE sector=?",
                    (legacy_sector,),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM role_baselines WHERE sector='private_school'"
                ).fetchone()[0],
                0,
            )
        self.assertEqual(
            self.admin_client().get("/api/baseline-setup").get_json()["status"],
            "not_configured",
        )

    def test_configured_higher_baseline_reduces_only_that_roles_deviation(self):
        training = pd.DataFrame([
            {"user": "alpha_1", "role": "alpha", "files_accessed": 10,
             "data_transferred_mb": 40, "failed_logins": 1, "off_hours_access": 0,
             "is_anomaly": 0},
            {"user": "alpha_2", "role": "alpha", "files_accessed": 10,
             "data_transferred_mb": 60, "failed_logins": 1, "off_hours_access": 0,
             "is_anomaly": 0},
            {"user": "beta_1", "role": "beta", "files_accessed": 10,
             "data_transferred_mb": 10, "failed_logins": 1, "off_hours_access": 0,
             "is_anomaly": 0},
            {"user": "beta_2", "role": "beta", "files_accessed": 10,
             "data_transferred_mb": 30, "failed_logins": 1, "off_hours_access": 0,
             "is_anomaly": 0},
        ])
        same_activity = pd.DataFrame([
            {"user": "alpha_target", "role": "alpha", "files_accessed": 10,
             "data_transferred_mb": 80, "failed_logins": 1, "off_hours_access": 0},
            {"user": "beta_target", "role": "beta", "files_accessed": 10,
             "data_transferred_mb": 25, "failed_logins": 1, "off_hours_access": 0},
        ])
        learned, overall = role_baseline.calculate_role_baselines(training)
        before = role_baseline.attach_baseline_deviations(
            same_activity, learned, overall
        ).set_index("role")["role_baseline_score"]
        with closing(database.connect()) as conn:
            conn.execute(
                "INSERT INTO baseline_setup_status (sector, completed) VALUES "
                "('private_school', 1)"
            )
            conn.execute(
                "INSERT INTO role_baselines (sector, role, feature, value) "
                "VALUES ('private_school', 'alpha', 'data_transferred_mb', 80)"
            )
            conn.commit()

        effective, fallback = role_baseline.get_effective_role_baselines(
            training, "private_school"
        )
        after = role_baseline.attach_baseline_deviations(
            same_activity, effective, fallback
        ).set_index("role")["role_baseline_score"]
        self.assertLess(after["alpha"], before["alpha"])
        self.assertEqual(after["beta"], before["beta"])
        self.assertEqual(effective["alpha"]["files_accessed"], learned["alpha"]["files_accessed"])

    def test_unconfigured_scores_match_learned_reference_and_observed_hints_are_read_only(self):
        generate()
        with closing(database.connect()) as conn:
            events = pd.read_sql("SELECT * FROM logs", conn)

        legacy_reference = model._run_ensemble(
            events.copy(),
        )
        self.assertFalse(
            role_baseline.get_declared_role_baselines("private_school")
        )

        from app import _baseline_setup_payload
        payload = _baseline_setup_payload("private_school")
        after_hint = model._run_ensemble(
            events.copy(),
        )
        np.testing.assert_allclose(
            legacy_reference["final_score"],
            after_hint["final_score"],
            rtol=0,
            atol=0,
        )
        response = self.admin_client().put(
            "/api/baseline-setup",
            json={
                "complete": True,
                "values": [
                    {"role": item["role"], "feature": item["feature"],
                     "value": item["default_value"]}
                    for item in payload["items"]
                ],
            },
        )
        self.assertEqual(response.status_code, 200)
        after_default_setup = model._run_ensemble(events.copy())
        np.testing.assert_allclose(
            legacy_reference["final_score"],
            after_default_setup["final_score"],
            rtol=0,
            atol=0,
        )

    def test_anomaly_detector_parameters_do_not_depend_on_baselines(self):
        generate()
        with closing(database.connect()) as conn:
            events = pd.read_sql("SELECT * FROM logs", conn)
        original_if = model.IsolationForest
        original_svm = model.OneClassSVM
        captured = []

        def make_if(**kwargs):
            captured.append(("if", kwargs.copy()))
            return original_if(**kwargs)

        def make_svm(**kwargs):
            captured.append(("svm", kwargs.copy()))
            return original_svm(**kwargs)

        with patch.object(model, "IsolationForest", side_effect=make_if), \
                patch.object(model, "OneClassSVM", side_effect=make_svm):
            model._run_ensemble(events.copy())
            unconfigured = list(captured)
            captured.clear()
            with closing(database.connect()) as conn:
                conn.execute(
                    "INSERT INTO baseline_setup_status (sector, completed) "
                    "VALUES ('private_school', 1)"
                )
                conn.execute(
                    "INSERT INTO role_baselines (sector, role, feature, value) "
                    "VALUES ('private_school', 'researcher', 'data_transferred_mb', 120)"
                )
                conn.commit()
            model._run_ensemble(events.copy())
            configured = list(captured)
        self.assertEqual(configured, unconfigured)
        self.assertEqual(model.CONTAMINATION, 0.08)
        self.assertEqual(model.IF_WEIGHT, 0.6)
        self.assertEqual(model.OCSVM_WEIGHT, 0.4)
        self.assertEqual(model.CALIBRATION_LOW_QUANTILE, 0.05)
        self.assertEqual(model.CALIBRATION_HIGH_QUANTILE, 0.95)

    def test_roles_outside_active_taxonomy_are_ignored(self):
        generate()
        with closing(database.connect()) as conn:
            conn.execute(
                "INSERT INTO logs (user, login_hour, files_accessed, data_transferred_mb, "
                "failed_logins, off_hours_access, role) "
                "VALUES ('outside_role', 12, 4, 10, 0, 0, 'outside_role')"
            )
            conn.commit()
        self.assertNotIn("outside_role", {row["user"] for row in model.score_users()})

    def test_doubling_or_halving_baseline_effect_stays_within_measured_regression_bound(self):
        for sector, config in SECTORS.items():
            with self.subTest(sector=sector):
                self.set_sector(sector)
                generate()
                with closing(database.connect()) as conn:
                    events = pd.read_sql("SELECT * FROM logs", conn)
                role = config["roles"][0]
                baseline = float(
                    events.loc[events["role"] == role, "data_transferred_mb"].mean()
                )
                score_sets = {}
                for case, multiplier in (("base", 1), ("double", 2), ("half", 0.5)):
                    with closing(database.connect()) as conn:
                        conn.execute(
                            "INSERT INTO baseline_setup_status (sector, completed) "
                            "VALUES (?, 1) ON CONFLICT(sector) DO UPDATE SET completed=1",
                            (sector,),
                        )
                        conn.execute(
                            "INSERT INTO role_baselines (sector, role, feature, value) "
                            "VALUES (?, ?, 'data_transferred_mb', ?) "
                            "ON CONFLICT(sector, role, feature) DO UPDATE SET value=excluded.value",
                            (sector, role, baseline * multiplier),
                        )
                        conn.commit()
                    scored = model._run_ensemble(events.copy())
                    score_sets[case] = scored.groupby(["user", "role"])["final_score"].mean()
                targeted = [
                    user for user, actual_role in score_sets["base"].index
                    if actual_role == role
                ]
                max_change = max(
                    abs(float(score_sets[case].loc[(user, role)] - score_sets["base"].loc[(user, role)]))
                    for case in ("double", "half")
                    for user in targeted
                )
                # Step B measured a maximum absolute user-score change of 0.685 points.
                self.assertLessEqual(max_change, 0.8)

    def admin_client(self, role="admin"):
        client = app.test_client()
        with client.session_transaction() as session:
            session["username"] = "baseline-admin" if role == "admin" else "test-user"
            session["role"] = role
        return client

    def complete_values(self, client):
        items = client.get("/api/baseline-setup").get_json()["items"]
        return [
            {"role": item["role"], "feature": item["feature"],
             "value": item["default_value"]}
            for item in items
        ]


class RoleBaselineApiTest(unittest.TestCase):
    def setUp(self):
        self.database_path = temporary_database_path()
        self.previous_database_path = database.DB_PATH
        database.DB_PATH = self.database_path
        database.ensure_database()
        init_users_table()
        self.set_sector("private_school")

    def tearDown(self):
        database.DB_PATH = self.previous_database_path
        if os.path.exists(self.database_path):
            os.remove(self.database_path)

    def set_sector(self, sector):
        with closing(database.connect()) as conn:
            conn.execute(
                "UPDATE deployment_config SET sector = ? WHERE id = 1",
                (sector,),
            )
            conn.commit()

    def admin_client(self, role="admin"):
        client = app.test_client()
        with client.session_transaction() as session:
            session["username"] = "baseline-admin" if role == "admin" else "test-user"
            session["role"] = role
        return client

    def complete_values(self, client):
        items = client.get("/api/baseline-setup").get_json()["items"]
        return [
            {"role": item["role"], "feature": item["feature"],
             "value": item["default_value"]}
            for item in items
        ]

    def test_admin_only_access_and_unauthenticated_rejection(self):
        self.assertEqual(app.test_client().get("/api/baseline-setup").status_code, 302)
        for role in ("management", "compliance"):
            with self.subTest(role=role):
                response = self.admin_client(role).get("/api/baseline-setup")
                self.assertEqual(response.status_code, 403)
        self.assertEqual(self.admin_client().get("/api/baseline-setup").status_code, 200)

    def test_put_is_atomic_for_invalid_entry_and_complete_requires_every_value(self):
        client = self.admin_client()
        values = self.complete_values(client)
        before = client.get("/api/baseline-setup").get_json()
        response = client.put(
            "/api/baseline-setup",
            json={"complete": False, "values": [values[0], {
                "role": "not-a-role", "feature": "files_accessed", "value": 3,
            }]},
        )
        self.assertEqual(response.status_code, 400)
        with closing(database.connect()) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM role_baselines").fetchone()[0],
                0,
            )
        incomplete = client.put(
            "/api/baseline-setup",
            json={"complete": True, "values": []},
        )
        self.assertEqual(incomplete.status_code, 400)
        self.assertEqual(
            client.get("/api/baseline-setup").get_json()["status"],
            before["status"],
        )

    def test_every_baseline_validation_rule_and_zero_acceptance(self):
        client = self.admin_client()
        bad_values = [
            ("unknown role", {"role": "x", "feature": "files_accessed", "value": 1}),
            ("unknown feature", {"role": "researcher", "feature": "x", "value": 1}),
            ("nan", {"role": "researcher", "feature": "files_accessed", "value": float("nan")}),
            ("infinity", {"role": "researcher", "feature": "files_accessed", "value": float("inf")}),
            ("string", {"role": "researcher", "feature": "files_accessed", "value": "4"}),
            ("boolean", {"role": "researcher", "feature": "files_accessed", "value": True}),
            ("negative", {"role": "researcher", "feature": "files_accessed", "value": -1}),
            ("below minimum", {"role": "researcher", "feature": "files_accessed", "value": -0.001}),
            ("above maximum", {"role": "researcher", "feature": "files_accessed", "value": 155.01}),
            ("off-hours above one", {"role": "researcher", "feature": "off_hours_access", "value": 1.01}),
        ]
        for label, entry in bad_values:
            with self.subTest(label=label):
                response = client.put(
                    "/api/baseline-setup",
                    json={"complete": False, "values": [entry]},
                )
                self.assertEqual(response.status_code, 400)
        for feature in role_baseline.ROLE_FIELDS:
            with self.subTest(zero_feature=feature):
                accepted = client.put(
                    "/api/baseline-setup",
                    json={"complete": False, "values": [{
                        "role": "researcher", "feature": feature, "value": 0,
                    }]},
                )
                self.assertEqual(accepted.status_code, 200)
                with closing(database.connect()) as conn:
                    value = conn.execute(
                        "SELECT value FROM role_baselines WHERE sector='private_school' "
                        "AND role='researcher' AND feature=?",
                        (feature,),
                    ).fetchone()[0]
                self.assertEqual(value, 0)

    def test_soft_warning_is_returned_and_does_not_block_save(self):
        self.set_sector("private_school")
        generate()
        client = self.admin_client()
        response = client.put(
            "/api/baseline-setup",
            json={"complete": False, "values": [{
                "role": "researcher", "feature": "data_transferred_mb", "value": 0,
            }]},
        )
        self.assertEqual(response.status_code, 200)
        item = next(
            item for item in client.get("/api/baseline-setup").get_json()["items"]
            if item["role"] == "researcher" and item["feature"] == "data_transferred_mb"
        )
        self.assertIn("less than one-fifth", item["warning"])

    def test_completion_update_audit_and_reset_transitions(self):
        client = self.admin_client()
        values = self.complete_values(client)
        complete = client.put(
            "/api/baseline-setup",
            json={"complete": True, "values": values},
        )
        self.assertEqual(complete.status_code, 200)
        self.assertTrue(complete.get_json()["configured"])
        self.assertEqual(client.get("/api/baseline-setup").get_json()["status"], "configured")

        selected = next(
            entry for entry in values
            if entry["role"] == "researcher" and entry["feature"] == "files_accessed"
        )
        updated = [
            {**entry, "value": entry["value"] + 2}
            if entry is selected else entry
            for entry in values
        ]
        update_response = client.put(
            "/api/baseline-setup",
            json={"complete": True, "values": updated},
        )
        self.assertEqual(update_response.status_code, 200)
        with closing(database.connect()) as conn:
            audit = conn.execute(
                "SELECT action, detail FROM audit_log WHERE action LIKE 'BASELINE_%'"
            ).fetchall()
        actions = [row[0] for row in audit]
        self.assertIn("BASELINE_SETUP_COMPLETED", actions)
        self.assertIn("BASELINE_UPDATED", actions)
        updated_detail = next(row[1] for row in audit if row[0] == "BASELINE_UPDATED")
        for expected in ("role=researcher", "feature=files_accessed", "admin=baseline-admin",
                         "sector=private_school", "old=", "new="):
            self.assertIn(expected, updated_detail)

        reset = client.post("/api/baseline-setup/reset")
        self.assertEqual(reset.status_code, 200)
        self.assertEqual(client.get("/api/baseline-setup").get_json()["status"], "not_configured")
        with closing(database.connect()) as conn:
            reset_actions = conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE action='BASELINE_RESET'"
            ).fetchone()[0]
        self.assertEqual(reset_actions, 1)


if __name__ == "__main__":
    unittest.main()
