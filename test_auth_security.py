import os
import tempfile
import unittest

import database
from auth import LOGIN_COOLDOWN_SECONDS, authenticate_login, init_users_table


class AuthSecurityTest(unittest.TestCase):
    def setUp(self):
        self.database_path = tempfile.mktemp(suffix=".db")
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


if __name__ == "__main__":
    unittest.main()
