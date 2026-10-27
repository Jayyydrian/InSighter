import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import bcrypt
import database
from auth import hash_password, init_users_table, verify_password
from test_support import TEST_SECRET


PROJECT_ROOT = Path(__file__).resolve().parent
OLD_USERS_SCHEMA = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin', 'management', 'compliance')),
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
)
"""
ACCOUNT_FIXTURE = (
    ("admin", "test-admin-password", "admin"),
    ("manager", "test-manager-password", "management"),
    ("hr_officer", "test-compliance-password", "compliance"),
)


class AccountRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="insighter-account-recovery-")
        self.database_path = Path(self.temp_dir.name) / "legacy.db"
        self.previous_database_path = database.DB_PATH
        database.DB_PATH = str(self.database_path)
        self.children = []
        with patch.dict(
            os.environ,
            {"INSIGHTER_ADMIN_USERNAME": "", "INSIGHTER_ADMIN_PASSWORD": ""},
        ):
            self._create_old_schema()

    def tearDown(self):
        database.DB_PATH = self.previous_database_path
        for child in self.children:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)
        self.temp_dir.cleanup()

    def _create_old_schema(self):
        conn = sqlite3.connect(self.database_path)
        try:
            conn.execute(OLD_USERS_SCHEMA)
            conn.executemany(
                "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                [
                    (name, hash_password(password), role)
                    for name, password, role in ACCOUNT_FIXTURE
                ],
            )
            conn.commit()
        finally:
            conn.close()

    def _run_script(self, *arguments, db_path=None):
        env = os.environ.copy()
        for name in tuple(env):
            if name.startswith("INSIGHTER_"):
                env.pop(name)
        env.update({
            "INSIGHTER_SECRET_KEY": TEST_SECRET,
            "INSIGHTER_PII_SECRET": TEST_SECRET,
            "INSIGHTER_DB_PATH": str(db_path or self.database_path),
            "INSIGHTER_PORT": "5051",
            "INSIGHTER_DISABLE_BACKGROUND_SIM": "1",
            "INSIGHTER_LLM_WARMUP": "0",
            "QT_QPA_PLATFORM": "offscreen",
            "USERPROFILE": str(Path(self.temp_dir.name) / "profile"),
        })
        command = [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "reactivate_accounts.py"),
            *arguments,
        ]
        child = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.children.append(child)
        stdout, stderr = child.communicate(timeout=15)
        return child.returncode, stdout, stderr

    def test_old_schema_migration_preserves_existing_users_and_csrf_logins(self):
        from app import app

        with patch.dict(
            os.environ,
            {"INSIGHTER_ADMIN_USERNAME": "", "INSIGHTER_ADMIN_PASSWORD": ""},
        ):
            init_users_table()
        conn = database.connect()
        try:
            states = conn.execute(
                "SELECT username, is_active FROM users ORDER BY username"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual([(row[0], row[1]) for row in states], [
            ("admin", 1),
            ("hr_officer", 1),
            ("manager", 1),
        ])

        for username, password, _role in ACCOUNT_FIXTURE:
            client = app.test_client()
            token = client.get("/api/csrf-token").get_json()["csrf_token"]
            response = client.post(
                "/login",
                data={
                    "username": username,
                    "password": password,
                    "_csrf_token": token,
                },
            )
            self.assertEqual(response.status_code, 302, username)
            self.assertEqual(client.get("/api/whoami").status_code, 200, username)

    def test_legacy_bcrypt_hashes_remain_unsupported(self):
        legacy_hash = bcrypt.hashpw(
            b"test-only-not-a-secret", bcrypt.gensalt()
        ).decode()
        self.assertFalse(verify_password("test-only-not-a-secret", legacy_hash))

    def test_fresh_database_and_existing_inactive_users_are_unchanged(self):
        fresh_path = Path(self.temp_dir.name) / "fresh.db"
        previous = database.DB_PATH
        database.DB_PATH = str(fresh_path)
        try:
            with patch.dict(
                os.environ,
                {"INSIGHTER_ADMIN_USERNAME": "", "INSIGHTER_ADMIN_PASSWORD": ""},
            ):
                init_users_table()
            conn = database.connect()
            try:
                conn.execute(
                    "INSERT INTO users (username, password_hash, role, is_active) "
                    "VALUES (?, ?, ?, 1)",
                    ("fresh-user", hash_password("fresh-user-password"), "admin"),
                )
                conn.commit()
            finally:
                conn.close()
            with patch.dict(
                os.environ,
                {"INSIGHTER_ADMIN_USERNAME": "", "INSIGHTER_ADMIN_PASSWORD": ""},
            ):
                init_users_table()
            conn = database.connect()
            try:
                active = conn.execute(
                    "SELECT is_active FROM users WHERE username = 'fresh-user'"
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(active, 1)
        finally:
            database.DB_PATH = previous

        conn = database.connect()
        try:
            conn.execute("ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
            conn.execute("UPDATE users SET is_active = 0")
            conn.commit()
        finally:
            conn.close()
        with patch.dict(
            os.environ,
            {"INSIGHTER_ADMIN_USERNAME": "", "INSIGHTER_ADMIN_PASSWORD": ""},
        ):
            init_users_table()
        conn = database.connect()
        try:
            states = conn.execute("SELECT is_active FROM users").fetchall()
        finally:
            conn.close()
        self.assertTrue(states)
        self.assertTrue(all(row[0] == 0 for row in states))

    def test_list_shows_account_status_without_password_hashes(self):
        returncode, stdout, stderr = self._run_script(
            "list", "--db", str(self.database_path)
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertIn("username\trole\tis_active\tis_demo", stdout)
        self.assertIn("admin\tadmin\t1\t0", stdout)
        self.assertNotIn("password_hash", stdout)
        self.assertNotIn("pbkdf2_sha256", stdout)

    def test_reactivate_creates_backup_and_audits_each_change(self):
        conn = database.connect()
        try:
            conn.execute("ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
            conn.execute("ALTER TABLE users ADD COLUMN is_demo INTEGER NOT NULL DEFAULT 0")
            conn.execute("UPDATE users SET is_active = 0")
            conn.execute("""
                CREATE TABLE audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT,
                    action TEXT NOT NULL,
                    detail TEXT DEFAULT '',
                    timestamp TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
        finally:
            conn.close()

        returncode, stdout, stderr = self._run_script(
            "reactivate", "admin", "manager", "hr_officer",
            "--db", str(self.database_path),
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertTrue(stdout.startswith(f"Database: {self.database_path.resolve()}"))
        backup_line = next(line for line in stdout.splitlines() if line.startswith("Backup: "))
        backup_path = Path(backup_line.removeprefix("Backup: "))
        self.assertTrue(backup_path.is_file())
        backup = sqlite3.connect(backup_path)
        try:
            before_states = backup.execute(
                "SELECT is_active FROM users ORDER BY username"
            ).fetchall()
        finally:
            backup.close()
        self.assertEqual([row[0] for row in before_states], [0, 0, 0])

        conn = database.connect()
        try:
            states = conn.execute(
                "SELECT username, is_active FROM users ORDER BY username"
            ).fetchall()
            audit = conn.execute(
                "SELECT username, action, detail FROM audit_log ORDER BY id"
            ).fetchall()
        finally:
            conn.close()
        self.assertTrue(all(row[1] == 1 for row in states))
        self.assertEqual(len(audit), 3)
        self.assertTrue(all(row[0] == "recovery-script" for row in audit))
        self.assertTrue(all(row[1] == "ACCOUNT_REACTIVATED" for row in audit))

    def test_unknown_user_is_refused_without_modifying_database(self):
        returncode, stdout, stderr = self._run_script(
            "reactivate", "not-a-user", "--db", str(self.database_path)
        )
        self.assertEqual(returncode, 2)
        self.assertTrue(stdout.startswith(f"Database: {self.database_path.resolve()}"))
        self.assertIn("Unknown username", stderr)
        self.assertFalse(any(".backup-" in path.name for path in self.database_path.parent.iterdir()))

    def test_script_requires_an_explicit_or_environment_database(self):
        env = os.environ.copy()
        for name in tuple(env):
            if name.startswith("INSIGHTER_"):
                env.pop(name)
        env.update({
            "INSIGHTER_SECRET_KEY": TEST_SECRET,
            "INSIGHTER_PII_SECRET": TEST_SECRET,
            "INSIGHTER_PORT": "5051",
            "INSIGHTER_DISABLE_BACKGROUND_SIM": "1",
            "INSIGHTER_LLM_WARMUP": "0",
            "QT_QPA_PLATFORM": "offscreen",
            "USERPROFILE": str(Path(self.temp_dir.name) / "profile"),
        })
        child = subprocess.Popen(
            [sys.executable, str(PROJECT_ROOT / "scripts" / "reactivate_accounts.py"), "list"],
            cwd=PROJECT_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.children.append(child)
        stdout, stderr = child.communicate(timeout=15)
        self.assertEqual(child.returncode, 2)
        self.assertEqual(stdout, "")
        self.assertIn("Provide --db", stderr)

    def test_script_refuses_repository_database_without_confirmation(self):
        returncode, _stdout, stderr = self._run_script(
            "list", "--db", str(PROJECT_ROOT / "database.db")
        )
        self.assertEqual(returncode, 2)
        self.assertIn("--i-know-this-is-my-real-db", stderr)


if __name__ == "__main__":
    unittest.main()
