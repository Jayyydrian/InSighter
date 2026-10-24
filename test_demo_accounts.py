import os
import unittest

import database
from auth import authenticate_login, hash_password, init_users_table
from scripts.demo_accounts import provision_demo_accounts, remove_demo_accounts
from test_support import create_test_user, temporary_database_path


class DemoAccountsTest(unittest.TestCase):
    def setUp(self):
        self.database_path = temporary_database_path()
        self.previous_database_path = database.DB_PATH
        database.DB_PATH = self.database_path
        init_users_table()

    def tearDown(self):
        database.DB_PATH = self.previous_database_path
        if os.path.exists(self.database_path):
            os.remove(self.database_path)

    def _create_pre_migration_database(self, include_is_active):
        path = temporary_database_path()
        previous_database_path = database.DB_PATH
        database.DB_PATH = path
        conn = database.connect()
        try:
            active_column = (
                ", is_active INTEGER NOT NULL DEFAULT 1" if include_is_active else ""
            )
            conn.execute(
                f"""CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL
                        CHECK(role IN ('admin', 'management', 'compliance')),
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    {active_column}
                )"""
            )
            conn.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                ("migration-admin", hash_password("migration-admin-password"), "admin"),
            )
            conn.commit()
        finally:
            conn.close()
            database.DB_PATH = previous_database_path
        return path

    def test_provisioning_creates_original_demo_accounts_and_roles(self):
        accounts = provision_demo_accounts()
        self.assertEqual(
            accounts,
            (
                ("admin", "admin123", "admin"),
                ("manager", "manager123", "management"),
                ("hr_officer", "hr_officer123", "compliance"),
            ),
        )

        conn = database.connect()
        try:
            users = conn.execute(
                "SELECT username, role, is_active, is_demo FROM users ORDER BY username"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(
            [tuple(row) for row in users],
            [
                ("admin", "admin", 1, 1),
                ("hr_officer", "compliance", 1, 1),
                ("manager", "management", 1, 1),
            ],
        )

        for username, password, role in accounts:
            user, retry_after = authenticate_login(username, password)
            self.assertEqual(user["role"], role)
            self.assertIsNone(retry_after)

    def test_provisioning_refuses_to_overwrite_a_real_account(self):
        create_test_user("admin", "a-real-account-password", "admin")

        with self.assertRaisesRegex(RuntimeError, "Refusing to overwrite"):
            provision_demo_accounts()

        conn = database.connect()
        try:
            user = conn.execute(
                "SELECT is_demo FROM users WHERE username = 'admin'"
            ).fetchone()
            count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(user[0], 0)
        self.assertEqual(count, 1)

    def test_removal_deletes_only_marked_demo_accounts(self):
        create_test_user("keep-user", "a-real-account-password", "management")
        provision_demo_accounts()

        self.assertEqual(remove_demo_accounts(), ("admin", "hr_officer", "manager"))

        conn = database.connect()
        try:
            users = conn.execute(
                "SELECT username, is_demo FROM users ORDER BY username"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual([tuple(row) for row in users], [("keep-user", 0)])

    def test_is_demo_migration_preserves_active_admin_and_allows_login(self):
        path = self._create_pre_migration_database(include_is_active=True)
        previous_database_path = database.DB_PATH
        database.DB_PATH = path
        try:
            init_users_table()
            conn = database.connect()
            try:
                columns = {
                    row[1]: row for row in conn.execute("PRAGMA table_info(users)")
                }
                user = conn.execute(
                    "SELECT id, role, is_active, is_demo FROM users "
                    "WHERE username = 'migration-admin'"
                ).fetchone()
            finally:
                conn.close()

            self.assertIn("is_demo", columns)
            self.assertEqual(columns["is_demo"][4], "0")
            self.assertEqual(tuple(user[1:]), ("admin", 1, 0))

            from app import app

            client = app.test_client()
            csrf_token = client.get("/api/csrf-token").get_json()["csrf_token"]
            response = client.post(
                "/login",
                data={
                    "username": "migration-admin",
                    "password": "migration-admin-password",
                    "_csrf_token": csrf_token,
                },
            )
            self.assertEqual(response.status_code, 302)
            self.assertEqual(client.get("/api/whoami").status_code, 200)
        finally:
            database.DB_PATH = previous_database_path
            if os.path.exists(path):
                os.remove(path)

    def test_old_schema_migration_keeps_existing_is_active_deactivation(self):
        path = self._create_pre_migration_database(include_is_active=False)
        previous_database_path = database.DB_PATH
        database.DB_PATH = path
        try:
            init_users_table()
            conn = database.connect()
            try:
                columns = {
                    row[1]: row for row in conn.execute("PRAGMA table_info(users)")
                }
                user = conn.execute(
                    "SELECT is_active, is_demo FROM users "
                    "WHERE username = 'migration-admin'"
                ).fetchone()
            finally:
                conn.close()

            self.assertIn("is_active", columns)
            self.assertIn("is_demo", columns)
            self.assertEqual(tuple(user), (0, 0))
        finally:
            database.DB_PATH = previous_database_path
            if os.path.exists(path):
                os.remove(path)


if __name__ == "__main__":
    unittest.main()
