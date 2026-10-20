import io
import os
import re
import unittest
from contextlib import closing, redirect_stdout
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("INSIGHTER_DISABLE_BACKGROUND_SIM", "1")

import database
import role_baseline
from auth import init_users_table
from desktop_app.uav_config_tab import UavConfigTab
from generate_logs import generate
from sector_config import DEFAULT_SECTOR, SECTORS, get_sector_config
from test_support import isolate_database, temporary_database_path

isolate_database()

from app import app
from PyQt6.QtWidgets import QApplication


def create_legacy_deployment_schema(conn):
    conn.execute("""
        CREATE TABLE deployment_config (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            sector TEXT DEFAULT 'sme_startup',
            threshold_high REAL DEFAULT 45,
            threshold_medium REAL DEFAULT 30,
            log_targets TEXT DEFAULT 'active_directory,google_workspace,microsoft_365',
            sync_interval_minutes INTEGER DEFAULT 15,
            drone_operator_username TEXT DEFAULT 'drone_operator',
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)


class SectorRemovalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.database_path = temporary_database_path()
        self.previous_database_path = database.DB_PATH
        database.DB_PATH = self.database_path

    def tearDown(self):
        database.DB_PATH = self.previous_database_path
        if os.path.exists(self.database_path):
            os.remove(self.database_path)

    def admin_client(self):
        client = app.test_client()
        with client.session_transaction() as session:
            session["username"] = "sector-admin"
            session["role"] = "admin"
        return client

    def test_default_sector_and_supported_taxonomy(self):
        supported = {"private_school", "uav_disaster_response"}
        expected = {
            "private_school": {
                "roles": ("researcher", "intern", "admin_staff", "it_staff"),
                "data_categories": (
                    "projects", "private_documents", "intellectual_property", "pii",
                ),
            },
            "uav_disaster_response": {
                "roles": ("drone_operator",),
                "data_categories": ("drone_platform_access", "disaster_data"),
            },
        }
        self.assertEqual(DEFAULT_SECTOR, "private_school")
        self.assertEqual(set(SECTORS), supported)
        self.assertEqual(
            {
                sector: {
                    "roles": get_sector_config(sector)["roles"],
                    "data_categories": get_sector_config(sector)["data_categories"],
                }
                for sector in SECTORS
            },
            expected,
        )

    def test_unsupported_sector_api_is_rejected(self):
        init_users_table()
        response = self.admin_client().post(
            "/api/deployment-config",
            json={"sector": "sme_startup"},
        )
        self.assertEqual(response.status_code, 400)
        message = response.get_json()["error"]
        self.assertIn("private_school", message)
        self.assertIn("uav_disaster_response", message)

    def test_legacy_sme_sector_migrates_once_and_preserves_data(self):
        with closing(database.connect()) as conn:
            create_legacy_deployment_schema(conn)
            conn.execute("INSERT INTO deployment_config (id, sector) VALUES (1, 'sme_startup')")
            conn.execute("CREATE TABLE logs (id INTEGER PRIMARY KEY, marker TEXT)")
            conn.execute("INSERT INTO logs (id, marker) VALUES (1, 'keep-log')")
            conn.execute("""
                CREATE TABLE role_baselines (
                    sector TEXT, role TEXT, feature TEXT, value REAL,
                    updated_by TEXT, updated_at TEXT,
                    PRIMARY KEY (sector, role, feature)
                )
            """)
            conn.execute("""
                CREATE TABLE baseline_setup_status (
                    sector TEXT PRIMARY KEY, completed INTEGER NOT NULL DEFAULT 0,
                    completed_by TEXT, completed_at TEXT
                )
            """)
            conn.execute(
                "INSERT INTO role_baselines (sector, role, feature, value) "
                "VALUES ('sme_startup', 'finance', 'files_accessed', 7)"
            )
            conn.execute(
                "INSERT INTO baseline_setup_status (sector, completed) "
                "VALUES ('sme_startup', 1)"
            )
            conn.commit()

        with self.assertLogs("auth", level="WARNING") as captured:
            init_users_table()
        self.assertEqual(len(captured.records), 1)
        self.assertIn("sme_startup", captured.records[0].getMessage())
        self.assertIn("private_school", captured.records[0].getMessage())

        with closing(database.connect()) as conn:
            first_state = (
                conn.execute("SELECT sector FROM deployment_config WHERE id=1").fetchone()[0],
                conn.execute("SELECT id, marker FROM logs").fetchall(),
                conn.execute(
                    "SELECT sector, role, feature, value FROM role_baselines"
                ).fetchall(),
                conn.execute(
                    "SELECT sector, completed FROM baseline_setup_status"
                ).fetchall(),
                conn.execute(
                    "SELECT username, action, detail FROM audit_log "
                    "WHERE action='SECTOR_MIGRATED'"
                ).fetchall(),
            )
        self.assertEqual(first_state[0], "private_school")
        self.assertEqual(first_state[1], [(1, "keep-log")])
        self.assertEqual(
            first_state[2],
            [("sme_startup", "finance", "files_accessed", 7.0)],
        )
        self.assertEqual(first_state[3], [("sme_startup", 1)])
        self.assertEqual(len(first_state[4]), 1)
        self.assertEqual(first_state[4][0][0], "system")

        init_users_table()
        with closing(database.connect()) as conn:
            second_state = (
                conn.execute("SELECT sector FROM deployment_config WHERE id=1").fetchone()[0],
                conn.execute("SELECT id, marker FROM logs").fetchall(),
                conn.execute(
                    "SELECT sector, role, feature, value FROM role_baselines"
                ).fetchall(),
                conn.execute(
                    "SELECT sector, completed FROM baseline_setup_status"
                ).fetchall(),
                conn.execute(
                    "SELECT username, action, detail FROM audit_log "
                    "WHERE action='SECTOR_MIGRATED'"
                ).fetchall(),
            )
        self.assertEqual(second_state, first_state)

    def test_null_and_empty_stored_sectors_migrate_without_error(self):
        for value in (None, ""):
            with self.subTest(value=value):
                with closing(database.connect()) as conn:
                    create_legacy_deployment_schema(conn)
                    conn.execute(
                        "INSERT INTO deployment_config (id, sector) VALUES (1, ?)",
                        (value,),
                    )
                    conn.commit()
                init_users_table()
                with closing(database.connect()) as conn:
                    sector = conn.execute(
                        "SELECT sector FROM deployment_config WHERE id=1"
                    ).fetchone()[0]
                    migrated = conn.execute(
                        "SELECT COUNT(*) FROM audit_log WHERE action='SECTOR_MIGRATED'"
                    ).fetchone()[0]
                self.assertEqual(sector, "private_school")
                self.assertEqual(migrated, 1)
                os.remove(self.database_path)

    def test_old_default_schema_legacy_copy_names_sector_explicitly(self):
        with closing(database.connect()) as conn:
            create_legacy_deployment_schema(conn)
            conn.execute("""
                CREATE TABLE uav_config (
                    id INTEGER PRIMARY KEY,
                    threshold_high REAL,
                    threshold_medium REAL,
                    log_targets TEXT,
                    sync_interval_minutes INTEGER,
                    drone_operator_username TEXT,
                    updated_at TEXT
                )
            """)
            conn.execute(
                "INSERT INTO uav_config VALUES "
                "(1, 45, 30, 'active_directory', 15, 'drone_operator', CURRENT_TIMESTAMP)"
            )
            conn.commit()

        statements = []
        original_connect = database.connect

        def traced_connect():
            conn = original_connect()
            conn.set_trace_callback(statements.append)
            return conn

        with patch("auth.connect", side_effect=traced_connect):
            init_users_table()

        with closing(database.connect()) as conn:
            sector = conn.execute(
                "SELECT sector FROM deployment_config WHERE id=1"
            ).fetchone()[0]
        self.assertEqual(sector, "private_school")
        self.assertNotEqual(sector, "sme_startup")
        legacy_insert = next(
            statement for statement in statements
            if statement.lstrip().upper().startswith("INSERT OR IGNORE INTO DEPLOYMENT_CONFIG")
        )
        self.assertRegex(
            legacy_insert,
            re.compile(
                r"INSERT OR IGNORE INTO deployment_config\s*\(\s*id\s*,\s*sector\s*,",
                re.IGNORECASE,
            ),
        )

    def test_seeded_row_counts_match_supported_sector_rosters(self):
        init_users_table()
        expected_counts = {
            "private_school": 1440,
            "uav_disaster_response": 720,
        }
        for sector, expected_count in expected_counts.items():
            with self.subTest(sector=sector):
                with closing(database.connect()) as conn:
                    conn.execute(
                        "UPDATE deployment_config SET sector=? WHERE id=1",
                        (sector,),
                    )
                    conn.commit()
                generate()
                with closing(database.connect()) as conn:
                    count = conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
                self.assertEqual(count, expected_count)

    def test_seed_without_deployment_configuration_uses_private_school_rows(self):
        output = io.StringIO()
        with redirect_stdout(output):
            generate()
        with closing(database.connect()) as conn:
            count = conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
            roles = {
                row[0] for row in conn.execute("SELECT DISTINCT role FROM logs")
            }
            users = conn.execute("SELECT COUNT(DISTINCT user) FROM logs").fetchone()[0]
        self.assertEqual(DEFAULT_SECTOR, "private_school")
        self.assertEqual(count, 1440)
        self.assertEqual(users, 12)
        self.assertEqual(roles, set(SECTORS["private_school"]["roles"]))
        self.assertIn("Seeded database with 1440 log entries for private_school.", output.getvalue())

    def test_desktop_config_lists_two_sectors_and_falls_back_to_private_school(self):
        tab = UavConfigTab(client=None)
        option_values = [
            tab.sector.itemData(index) for index in range(tab.sector.count())
        ]
        self.assertEqual(option_values, ["private_school", "uav_disaster_response"])

        tab._apply_config({
            "sector": "sme_startup",
            "taxonomy": {},
            "threshold_high": 45,
            "threshold_medium": 30,
            "sync_interval_minutes": 15,
            "drone_operator_username": "drone_operator",
            "log_targets": "active_directory",
            "updated_at": "test",
        })
        self.assertEqual(tab.sector.currentData(), "private_school")

    def test_data_transfer_baseline_maximum_is_1202_mb_per_event(self):
        spec = role_baseline.ROLE_BASELINE_FEATURES["data_transferred_mb"]
        self.assertEqual(spec["max"], 1202.0)
        self.assertEqual(spec["unit"], "MB/event")


if __name__ == "__main__":
    unittest.main()
