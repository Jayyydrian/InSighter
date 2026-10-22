import os
import tempfile
import unittest
from http.cookies import SimpleCookie
from unittest.mock import patch

os.environ["INSIGHTER_DISABLE_BACKGROUND_SIM"] = "1"
os.environ["INSIGHTER_LLM_WARMUP"] = "0"

import database
from auth import LOGIN_COOLDOWN_SECONDS, authenticate_login, hash_password, init_users_table, verify_password
from test_support import create_test_user, temporary_database_path


class AuthSecurityTest(unittest.TestCase):
    def setUp(self):
        self.database_path = temporary_database_path()
        self.previous_database_path = database.DB_PATH
        database.DB_PATH = self.database_path
        init_users_table()
        create_test_user("audit-admin", "a-long-test-password-123", "admin")

    def tearDown(self):
        database.DB_PATH = self.previous_database_path
        if os.path.exists(self.database_path):
            os.remove(self.database_path)

    def test_three_failed_attempts_trigger_cooldown(self):
        for _ in range(2):
            role, retry_after = authenticate_login("audit-admin", "wrong-password")
            self.assertIsNone(role)
            self.assertIsNone(retry_after)

        role, retry_after = authenticate_login("audit-admin", "wrong-password")
        self.assertIsNone(role)
        self.assertEqual(retry_after, LOGIN_COOLDOWN_SECONDS)

        role, retry_after = authenticate_login("audit-admin", "a-long-test-password-123")
        self.assertIsNone(role)
        self.assertGreater(retry_after, 0)

    def test_successful_login_clears_failed_attempts(self):
        authenticate_login("audit-admin", "wrong-password")
        role, retry_after = authenticate_login("audit-admin", "a-long-test-password-123")
        self.assertEqual(role["role"], "admin")
        self.assertIsNone(retry_after)

        role, retry_after = authenticate_login("audit-admin", "wrong-password")
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
        from auth import hash_password
        admin_id = create_test_user("audit-admin", "a-long-test-password-123", "admin")
        with patch("app.authenticate_login", return_value=({"id": admin_id, "username": "audit-admin", "role": "admin"}, None)), \
                patch("app.log_action"):
            client = app.test_client()
            token = client.get("/api/csrf-token").get_json()["csrf_token"]
            response = client.post("/login", data={
                "username": "audit-admin", "password": "test-password", "_csrf_token": token,
            })
        self.assertIn("SameSite=Lax", response.headers["Set-Cookie"])
        self.assertIn("HttpOnly", response.headers["Set-Cookie"])

    def test_password_hash_uses_unique_salts_and_constant_time_verifier(self):
        first = hash_password("a sufficiently long test password")
        second = hash_password("a sufficiently long test password")
        self.assertNotEqual(first.split("$")[2], second.split("$")[2])
        self.assertTrue(verify_password("a sufficiently long test password", first))
        self.assertFalse(verify_password("wrong", first))
        self.assertTrue(first.startswith("pbkdf2_sha256$600000$"))

    def test_csrf_is_required_for_mutations_and_get_cannot_mutate(self):
        from app import app

        client = app.test_client()
        self.assertEqual(client.post("/api/reseed").status_code, 400)
        self.assertEqual(client.get("/api/reseed").status_code, 405)
        self.assertEqual(client.get("/api/simulate").status_code, 405)

    def test_login_rotates_session_and_logout_revokes_old_cookie(self):
        from app import app

        client = app.test_client()
        csrf = client.get("/api/csrf-token").get_json()["csrf_token"]
        prelogin_cookie = SimpleCookie(client.get_cookie("session").value)
        prelogin_sid = client.get_cookie("session").value
        response = client.post("/login", data={
            "username": "audit-admin", "password": "a-long-test-password-123", "_csrf_token": csrf,
        })
        self.assertEqual(response.status_code, 302)
        authenticated_sid = client.get_cookie("session").value
        self.assertNotEqual(prelogin_sid, authenticated_sid)
        token = client.get("/api/whoami").status_code
        self.assertEqual(token, 200)

        replay_client = app.test_client()
        replay_client.set_cookie("session", authenticated_sid)
        csrf = client.get("/api/csrf-token").get_json()["csrf_token"]
        client.post("/logout", headers={"X-CSRF-Token": csrf})
        self.assertEqual(replay_client.get("/api/whoami").status_code, 302)
        self.assertEqual(client.get("/api/whoami").status_code, 302)

    def test_external_next_redirect_is_rejected(self):
        from app import app

        client = app.test_client()
        csrf = client.get("/api/csrf-token").get_json()["csrf_token"]
        response = client.post(
            "/login?next=https://attacker.example/path",
            data={"username": "audit-admin", "password": "a-long-test-password-123", "_csrf_token": csrf},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")

    def test_admin_permission_is_reloaded_from_database(self):
        from app import app

        client = app.test_client()
        csrf = client.get("/api/csrf-token").get_json()["csrf_token"]
        client.post("/login", data={
            "username": "audit-admin", "password": "a-long-test-password-123", "_csrf_token": csrf,
        })
        conn = database.connect()
        try:
            conn.execute("UPDATE users SET role = 'management' WHERE username = 'audit-admin'")
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(client.get("/api/audit-log").status_code, 403)


if __name__ == "__main__":
    unittest.main()
