import os
import tempfile
import unittest
from unittest.mock import patch

import database
from auth import LOGIN_COOLDOWN_SECONDS, authenticate_login, init_users_table
from test_support import temporary_database_path


class AuthSecurityTest(unittest.TestCase):
    def setUp(self):
        self.database_path = temporary_database_path()
        self.previous_database_path = database.DB_PATH
        database.DB_PATH = self.database_path
        init_users_table()

    def tearDown(self):
        database.DB_PATH = self.previous_database_path
        if os.path.exists(self.database_path):
            os.remove(self.database_path)

    def test_three_failed_attempts_trigger_cooldown(self):
        for _ in range(2):
            role, retry_after = authenticate_login("admin", "wrong-password")
            self.assertIsNone(role)
            self.assertIsNone(retry_after)

        role, retry_after = authenticate_login("admin", "wrong-password")
        self.assertIsNone(role)
        self.assertEqual(retry_after, LOGIN_COOLDOWN_SECONDS)

        role, retry_after = authenticate_login("admin", "admin123")
        self.assertIsNone(role)
        self.assertGreater(retry_after, 0)

    def test_successful_login_clears_failed_attempts(self):
        authenticate_login("admin", "wrong-password")
        role, retry_after = authenticate_login("admin", "admin123")
        self.assertEqual(role, "admin")
        self.assertIsNone(retry_after)

        role, retry_after = authenticate_login("admin", "wrong-password")
        self.assertIsNone(role)
        self.assertIsNone(retry_after)

    def test_unknown_accounts_use_same_lockout_policy(self):
        for _ in range(2):
            role, retry_after = authenticate_login("not-a-user", "wrong-password")
            self.assertIsNone(role)
            self.assertIsNone(retry_after)

        role, retry_after = authenticate_login("not-a-user", "wrong-password")
        self.assertIsNone(role)
        self.assertEqual(retry_after, LOGIN_COOLDOWN_SECONDS)

    def test_session_cookie_uses_lax_samesite_policy(self):
        from app import app

        self.assertEqual(app.config["SESSION_COOKIE_SAMESITE"], "Lax")
        with patch("app.authenticate_login", return_value=("admin", None)), \
                patch("app.log_action"):
            response = app.test_client().post(
                "/login",
                data={"username": "admin", "password": "test-password"},
            )
        self.assertIn("SameSite=Lax", response.headers["Set-Cookie"])


if __name__ == "__main__":
    unittest.main()
