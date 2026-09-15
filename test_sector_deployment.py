import os
import tempfile
import unittest

import pandas as pd

import database
from auth import init_users_table
from generate_logs import generate
from ingestion import store_events
from model import _run_ensemble, inject_live_event, score_users
from sector_config import SECTORS, get_demo_roster, monitoring_scope


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
        conn = database.connect()
        conn.execute("UPDATE deployment_config SET sector = ? WHERE id = 1", (sector,))
        conn.commit()
        conn.close()

    def test_reseed_roles_and_role_group_scoring_match_each_sector(self):
        for sector, config in SECTORS.items():
            self._set_sector(sector)
            generate()
            conn = database.connect()
            rows = conn.execute("SELECT DISTINCT role, data_category FROM logs").fetchall()
            event_count = conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
            conn.close()
            self.assertTrue({row[0] for row in rows}.issubset(set(config["roles"])))
            self.assertTrue({row[1] for row in rows}.issubset(set(config["data_categories"])))
            score_conn = database.connect()
            scored = _run_ensemble(pd.read_sql(
                "SELECT user, login_hour, files_accessed, data_transferred_mb, "
                "failed_logins, off_hours_access, role FROM logs",
                score_conn,
            ))
            score_conn.close()
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
        conn = database.connect()
        conn.execute(
            "INSERT INTO logs (user, role, data_category) VALUES (?,?,?)",
            ("outside", "researcher", "financial_transactions"),
        )
        conn.commit()
        result = monitoring_scope(conn)
        conn.close()
        self.assertEqual(result["total_events"], 1501)
        self.assertAlmostEqual(result["in_scope_percent"], round(1500 / 1501 * 100, 1))

    def test_live_ticks_and_role_risk_view_use_active_sector_roster(self):
        for sector, config in SECTORS.items():
            self._set_sector(sector)
            generate()
            for _ in range(12):
                inject_live_event()

            roster = get_demo_roster(sector)
            conn = database.connect()
            rows = conn.execute("SELECT DISTINCT user, role FROM logs").fetchall()
            conn.close()
            self.assertTrue({row[0] for row in rows}.issubset(set(roster)))
            self.assertTrue({(row[0], row[1]) for row in rows}.issubset(set(roster.items())))
            self.assertTrue({row[1] for row in rows}.issubset(set(config["roles"])))

            scored_users = score_users()
            self.assertTrue({row["role"] for row in scored_users}.issubset(set(config["roles"])))

    def test_live_ticks_switch_to_current_sector_before_reseed(self):
        self._set_sector("private_school")
        generate()
        self._set_sector("uav_disaster_response")
        conn = database.connect()
        previous_count = conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
        conn.close()
        for _ in range(20):
            inject_live_event()

        roster = get_demo_roster("uav_disaster_response")
        conn = database.connect()
        live_rows = conn.execute(
            "SELECT DISTINCT user, role FROM logs WHERE id > ?", (previous_count,)
        ).fetchall()
        conn.close()
        self.assertTrue({(row[0], row[1]) for row in live_rows}.issubset(set(roster.items())))

    def test_switching_and_reseeding_replaces_previous_roster(self):
        self._set_sector("private_school")
        generate()
        self._set_sector("uav_disaster_response")
        generate()

        roster = get_demo_roster("uav_disaster_response")
        conn = database.connect()
        rows = conn.execute("SELECT DISTINCT user, role FROM logs").fetchall()
        conn.close()
        self.assertEqual({row[0] for row in rows}, set(roster))
        self.assertTrue({(row[0], row[1]) for row in rows}.issubset(set(roster.items())))


if __name__ == "__main__":
    unittest.main()
