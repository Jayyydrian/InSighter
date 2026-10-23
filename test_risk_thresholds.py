import json
import os
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from test_support import isolate_database, temporary_database_path

isolate_database()
os.environ.setdefault("INSIGHTER_DISABLE_BACKGROUND_SIM", "1")
os.environ.setdefault("INSIGHTER_LLM_WARMUP", "0")

import database
import model
import risk_thresholds
from app import app
from auth import init_users_table
from desktop_app.risk import risk_level_for as desktop_risk_level_for
from privacy import hash_identifier
from risk_thresholds import DEFAULT_THRESHOLDS, get_thresholds, risk_level_for


class RiskThresholdTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app

    def setUp(self):
        self.previous_database_path = database.DB_PATH
        self.database_path = temporary_database_path()
        database.DB_PATH = self.database_path
        database.ensure_database()
        init_users_table()
        self.admin = self.app.test_client()
        with self.admin.session_transaction() as session:
            session["username"] = "threshold-admin"
            session["role"] = "admin"

    def tearDown(self):
        database.DB_PATH = self.previous_database_path
        if os.path.exists(self.database_path):
            os.remove(self.database_path)

    def test_default_pair_and_boundary_semantics(self):
        self.assertEqual(DEFAULT_THRESHOLDS, (45.0, 30.0))
        values = (
            (39.9, "LOW"),
            (40.0, "MEDIUM"),
            (49.9, "MEDIUM"),
            (50.0, "HIGH"),
        )
        for score, expected in values:
            with self.subTest(score=score):
                self.assertEqual(risk_level_for(score, 50, 40), expected)
                self.assertEqual(
                    desktop_risk_level_for(score, 50, 40), expected
                )

    def test_stored_thresholds_are_read_and_take_effect_on_next_scores_request(self):
        with closing(database.connect()) as conn:
            conn.execute(
                "UPDATE deployment_config SET threshold_high=45, threshold_medium=30 "
                "WHERE id=1"
            )
            conn.execute("""
                CREATE TABLE logs (
                    id INTEGER PRIMARY KEY,
                    user TEXT,
                    login_hour INTEGER,
                    files_accessed INTEGER,
                    data_transferred_mb REAL,
                    failed_logins INTEGER,
                    off_hours_access INTEGER,
                    role TEXT,
                    data_category TEXT
                )
            """)
            for row_id, username in enumerate(
                ("score_39", "score_40", "score_45", "score_50"), start=1
            ):
                conn.execute(
                    "INSERT INTO logs VALUES (?, ?, 12, 1, 1, 0, 0, 'researcher', 'projects')",
                    (row_id, username),
                )
            conn.commit()

        score_by_user = {
            "score_39": 39.9,
            "score_40": 40.0,
            "score_45": 45.0,
            "score_50": 50.0,
        }

        def deterministic_ensemble(frame, **_kwargs):
            result = frame.copy()
            result["if_score"] = 10.0
            result["ocsvm_score"] = 20.0
            result["is_anomaly"] = 0
            result["role_baseline_score"] = 0.0
            result["final_score"] = result["user"].map(score_by_user)
            return result

        with patch.object(
            model, "_run_ensemble", side_effect=deterministic_ensemble
        ):
            first = self.admin.get("/api/scores").get_json()
            self.assertEqual(
                {row["user"]: row["risk_level"] for row in first},
                {
                    "score_39": "MEDIUM",
                    "score_40": "MEDIUM",
                    "score_45": "HIGH",
                    "score_50": "HIGH",
                },
            )

            update = self.admin.post(
                "/api/deployment-config",
                json={
                    "sector": "private_school",
                    "threshold_high": 50,
                    "threshold_medium": 40,
                },
            )
            self.assertEqual(update.status_code, 200)
            second = self.admin.get("/api/scores").get_json()
            self.assertEqual(
                {row["user"]: row["risk_level"] for row in second},
                {
                    "score_39": "LOW",
                    "score_40": "MEDIUM",
                    "score_45": "MEDIUM",
                    "score_50": "HIGH",
                },
            )
            summary = self.admin.get("/api/summary").get_json()

        expected_counts = {
            level: sum(row["risk_level"] == level for row in second)
            for level in ("HIGH", "MEDIUM", "LOW")
        }
        self.assertEqual(summary, expected_counts)

    def test_invalid_or_missing_config_falls_back_once_with_warning(self):
        invalid_values = (
            (None, 30),
            (40, 40),
            (101, 30),
        )
        for high, medium in invalid_values:
            with self.subTest(high=high, medium=medium):
                with closing(database.connect()) as conn:
                    conn.execute(
                        "UPDATE deployment_config SET threshold_high=?, "
                        "threshold_medium=? WHERE id=1",
                        (high, medium),
                    )
                    conn.commit()
                    risk_thresholds._fallback_warning_logged = False
                    with self.assertLogs("risk_thresholds", level="WARNING") as logs:
                        self.assertEqual(get_thresholds(conn), DEFAULT_THRESHOLDS)
                        self.assertEqual(get_thresholds(conn), DEFAULT_THRESHOLDS)
                self.assertEqual(len(logs.output), 1)

        with closing(database.connect()) as conn:
            conn.execute("DELETE FROM deployment_config")
            conn.commit()
            risk_thresholds._fallback_warning_logged = False
            with self.assertLogs("risk_thresholds", level="WARNING") as logs:
                self.assertEqual(get_thresholds(conn), DEFAULT_THRESHOLDS)
            self.assertEqual(len(logs.output), 1)

    def test_missing_config_table_falls_back_without_crashing(self):
        with closing(database.connect()) as conn:
            conn.execute("DROP TABLE deployment_config")
            conn.commit()
            risk_thresholds._fallback_warning_logged = False
            with self.assertLogs("risk_thresholds", level="WARNING") as logs:
                self.assertEqual(get_thresholds(conn), DEFAULT_THRESHOLDS)
            self.assertEqual(len(logs.output), 1)

    def test_threshold_post_validation_rejects_invalid_payload_without_writes(self):
        valid = {
            "sector": "private_school",
            "threshold_high": 50,
            "threshold_medium": 40,
        }
        invalid_payloads = (
            {"sector": "private_school"},
            {**valid, "threshold_high": "50"},
            {**valid, "threshold_high": True},
            {**valid, "threshold_high": float("nan")},
            {**valid, "threshold_high": float("inf")},
            {**valid, "threshold_high": -1, "threshold_medium": -2},
            {**valid, "threshold_medium": 0},
            {**valid, "threshold_medium": 50},
            {**valid, "threshold_high": 101},
        )
        with closing(database.connect()) as conn:
            conn.execute(
                "UPDATE deployment_config SET threshold_high=45, threshold_medium=30 "
                "WHERE id=1"
            )
            conn.commit()

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                response = self.admin.post(
                    "/api/deployment-config",
                    data=json.dumps(payload),
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn("error", response.get_json())
                with closing(database.connect()) as conn:
                    self.assertEqual(
                        conn.execute(
                            "SELECT threshold_high, threshold_medium "
                            "FROM deployment_config WHERE id=1"
                        ).fetchone(),
                        (45.0, 30.0),
                    )
                    self.assertEqual(
                        conn.execute(
                            "SELECT COUNT(*) FROM audit_log "
                            "WHERE action='CONFIG_UPDATED'"
                        ).fetchone()[0],
                        0,
                    )

    def test_threshold_change_audit_records_old_and_new_values(self):
        response = self.admin.post(
            "/api/deployment-config",
            json={
                "sector": "private_school",
                "threshold_high": 55,
                "threshold_medium": 35,
            },
        )
        self.assertEqual(response.status_code, 200)
        with closing(database.connect()) as conn:
            action, detail = conn.execute(
                "SELECT action, detail FROM audit_log "
                "WHERE action='CONFIG_UPDATED' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertEqual(action, "CONFIG_UPDATED")
        self.assertIn("threshold_high=45.0->55.0", detail)
        self.assertIn("threshold_medium=30.0->35.0", detail)


class RiskScoreGoldenTest(unittest.TestCase):
    def setUp(self):
        self.previous_database_path = database.DB_PATH
        self.database_path = temporary_database_path()
        database.DB_PATH = self.database_path
        database.ensure_database()
        init_users_table()

    def tearDown(self):
        database.DB_PATH = self.previous_database_path
        if os.path.exists(self.database_path):
            os.remove(self.database_path)

    def test_default_scores_match_base_snapshot_and_repeat_stably(self):
        golden_path = Path(__file__).parent / "tests" / "data" / "risk_score_golden.json"
        golden = json.loads(golden_path.read_text(encoding="utf-8"))
        self.assertEqual(golden["source_commit"], "de94a6c0c3b6d72d9e4c5a1334a61e274edd16ec")

        from generate_logs import generate
        from sector_config import SECTORS

        for sector in SECTORS:
            with self.subTest(sector=sector):
                with closing(database.connect()) as conn:
                    conn.execute(
                        "UPDATE deployment_config SET sector=?, threshold_high=45, "
                        "threshold_medium=30 WHERE id=1",
                        (sector,),
                    )
                    conn.commit()
                generate()
                first = model.score_users()
                second = model.score_users()
                self.assertEqual(first, second)
                expected = {
                    row["user"]: row
                    for row in golden["sectors"][sector]
                }
                self.assertEqual(
                    {row["user"] for row in first},
                    set(expected),
                )
                for row in first:
                    base = expected[row["user"]]
                    self.assertEqual(row["risk_level"], base["risk_level"])
                    self.assertLessEqual(
                        abs(row["risk_score"] - base["risk_score"]), 0.05
                    )
