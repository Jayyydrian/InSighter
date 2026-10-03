import os
import tempfile
import unittest
from contextlib import closing

import pandas as pd

import database
os.environ["INSIGHTER_DISABLE_BACKGROUND_SIM"] = "1"
from auth import init_users_table
from generate_logs import EVENTS_PER_USER, generate
from ingestion import store_events
from model import _run_ensemble, get_recent_alerts, inject_live_event, score_users
from sector_config import SECTORS, get_sector_roster, monitoring_scope
from app import app


class SectorDeploymentTest(unittest.TestCase):
    def setUp(self):
        self.database_path = tempfile.mktemp(suffix=".db")
        self.previous_database_path = database.DB_PATH
        database.DB_PATH = self.database_path
        init_users_table()

    def tearDown(self):
        database.DB_PATH = self.previous_database_path
        if os.path.exists(self.database_path):
            os.remove(self.database_path)

    def _set_sector(self, sector):
        with closing(database.connect()) as conn:
            conn.execute("UPDATE deployment_config SET sector = ? WHERE id = 1", (sector,))
            conn.commit()

    def test_reseed_roles_and_role_group_scoring_match_each_sector(self):
        for sector, config in SECTORS.items():
            self._set_sector(sector)
            generate()
            with closing(database.connect()) as conn:
                rows = conn.execute("SELECT DISTINCT user, role, data_category FROM logs").fetchall()
                event_count = conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
            self.assertEqual({row[0] for row in rows}, {user for user, _ in get_sector_roster(sector)})
            self.assertTrue({row[1] for row in rows}.issubset(set(config["roles"])))
            self.assertTrue({row[2] for row in rows}.issubset(set(config["data_categories"])))
            expected_distribution = config["roster_distribution"]
            with closing(database.connect()) as role_conn:
                actual_distribution = dict(role_conn.execute(
                    "SELECT role, COUNT(DISTINCT user) FROM logs GROUP BY role"
                ).fetchall())
            self.assertEqual(actual_distribution, expected_distribution)
            self.assertNotEqual(len(rows), 5)
            with closing(database.connect()) as score_conn:
                scored = _run_ensemble(pd.read_sql(
                    "SELECT user, login_hour, files_accessed, data_transferred_mb, "
                    "failed_logins, off_hours_access, role FROM logs",
                    score_conn,
                ))
            self.assertEqual(len(scored), event_count)

    def test_invalid_role_is_rejected_for_active_sector(self):
        self._set_sector("private_school")
        generate()
        with self.assertRaisesRegex(ValueError, "not allowed"):
            store_events([{
                "user": "outside-role",
                "role": "finance",
                "login_hour": 10,
                "data_category": "projects",
            }])

    def test_monitoring_scope_reports_in_scope_percentage(self):
        self._set_sector("private_school")
        generate()
        with closing(database.connect()) as conn:
            conn.execute(
                "INSERT INTO logs (user, role, data_category) VALUES (?,?,?)",
                ("outside", "researcher", "financial_transactions"),
            )
            conn.commit()
            result = monitoring_scope(conn)
        seeded_events = EVENTS_PER_USER * len(get_sector_roster("private_school"))
        self.assertEqual(result["total_events"], seeded_events + 1)
        self.assertAlmostEqual(result["in_scope_percent"], round(seeded_events / (seeded_events + 1) * 100, 1))

    def test_sector_switch_filters_role_behavior_without_reseeding(self):
        self._set_sector("private_school")
        generate()
        self._set_sector("uav_disaster_response")

        client = app.test_client()
        with client.session_transaction() as session:
            session["username"] = "admin"
            session["role"] = "admin"
        response = client.get("/api/scores")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), [])
        with closing(database.connect()) as stale_conn:
            stale_roles = {row[0] for row in stale_conn.execute("SELECT DISTINCT role FROM logs")}
        self.assertEqual(stale_roles, {"researcher", "intern", "admin_staff", "it_staff"})

    def test_live_simulation_varies_hot_users_and_activity(self):
        self._set_sector("sme_startup")
        generate()
        scores_before = {row["user"]: row["risk_score"] for row in score_users()}
        observed = [inject_live_event() for _ in range(40)]
        scores_after = {row["user"]: row["risk_score"] for row in score_users()}
        anomalies = [event for event in observed if event["anomalous"]]
        deltas = {
            round(scores_after[user] - score, 1)
            for user, score in scores_before.items()
            if user in scores_after and round(scores_after[user] - score, 1) != 0
        }

        self.assertGreaterEqual(len({event["user"] for event in anomalies}), 2)
        self.assertGreater(len({tuple(event["activity"].values()) for event in anomalies}), 1)
        self.assertGreater(len({tuple(event["activity"].values()) for event in observed}), 20)
        self.assertGreaterEqual(len(deltas), 2)

    def test_uav_alerts_use_disaster_response_language(self):
        self._set_sector("uav_disaster_response")
        generate()
        with closing(database.connect()) as conn:
            conn.execute(
                """INSERT INTO logs (user, login_hour, files_accessed, data_transferred_mb,
                   failed_logins, off_hours_access, role, data_category, source)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                ("drone_operator_01", 2, 92, 654, 6, 1, "drone_operator", "disaster_data", "synthetic"),
            )
            conn.commit()

        descriptions = [alert["description"] for alert in get_recent_alerts()]
        self.assertTrue(any("disaster survivor records" in text and "triage data" in text for text in descriptions))


if __name__ == "__main__":
    unittest.main()
