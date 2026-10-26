import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("INSIGHTER_DISABLE_BACKGROUND_SIM", "1")
os.environ.setdefault("INSIGHTER_LLM_WARMUP", "0")

from test_support import create_test_user, isolate_database, temporary_database_path

isolate_database()

import app as app_module
import database
from auth import init_users_table


class DesktopOnlyHttpContractTests(unittest.TestCase):
    def setUp(self):
        self.previous_database_path = database.DB_PATH
        self.database_path = temporary_database_path()
        database.DB_PATH = self.database_path
        database.ensure_database()
        init_users_table()
        create_test_user(
            "desktop-only-admin",
            "desktop-only-long-test-password",
            "admin",
        )
        self.client = app_module.app.test_client()

    def tearDown(self):
        database.DB_PATH = self.previous_database_path
        if os.path.exists(self.database_path):
            os.remove(self.database_path)

    def test_removed_pages_are_non_500_and_login_get_is_non_html(self):
        self.assertEqual(self.client.get("/").status_code, 404)

        login_response = self.client.get("/login")
        self.assertEqual(login_response.status_code, 200)
        self.assertEqual(login_response.mimetype, "application/json")
        self.assertEqual(
            login_response.get_json(),
            {"message": "Sign in through the InSighter desktop application."},
        )

    def test_desktop_login_csrf_and_unauthenticated_redirect_are_unchanged(self):
        unauthenticated = self.client.get("/api/scores")
        self.assertEqual(unauthenticated.status_code, 302)
        self.assertEqual(
            unauthenticated.headers["Location"],
            "/login?next=/api/scores",
        )

        csrf = self.client.get("/api/csrf-token").get_json()["csrf_token"]
        missing_csrf = self.client.post(
            "/login",
            data={
                "username": "desktop-only-admin",
                "password": "desktop-only-long-test-password",
            },
        )
        self.assertEqual(missing_csrf.status_code, 400)

        login_response = self.client.post(
            "/login",
            data={
                "username": "desktop-only-admin",
                "password": "desktop-only-long-test-password",
                "_csrf_token": csrf,
            },
        )
        self.assertEqual(login_response.status_code, 302)
        self.assertEqual(login_response.headers["Location"], "/")
        self.assertEqual(self.client.get("/api/whoami").status_code, 200)
