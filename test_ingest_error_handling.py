import os
import unittest

import database
from auth import init_users_table
from generate_logs import generate
from ingestion import ensure_logs_schema
from test_support import create_test_user, temporary_database_path

os.environ["INSIGHTER_DISABLE_BACKGROUND_SIM"] = "1"
os.environ["INSIGHTER_LLM_WARMUP"] = "0"

from app import app


class IngestErrorHandlingTest(unittest.TestCase):
    def setUp(self):
        self.database_path = temporary_database_path()
        self.previous_database_path = database.DB_PATH
        database.DB_PATH = self.database_path
        database.ensure_database()
        generate()
        init_users_table()
        ensure_logs_schema()
        self.admin_password = "ingest-admin-test-password"
        create_test_user("ingest-admin", self.admin_password, "admin")
        self.admin_client = app.test_client()
        self._login(self.admin_client, "ingest-admin", self.admin_password)

    def tearDown(self):
        database.DB_PATH = self.previous_database_path
        if os.path.exists(self.database_path):
            os.remove(self.database_path)

    @staticmethod
    def _login(client, username, password):
        csrf = client.get("/api/csrf-token").get_json()["csrf_token"]
        response = client.post(
            "/login",
            data={
                "username": username,
                "password": password,
                "_csrf_token": csrf,
            },
        )
        if response.status_code != 302:
            raise AssertionError(f"Login failed for test user {username}.")

    def _csrf(self, client):
        return client.get("/api/csrf-token").get_json()["csrf_token"]

    def test_invalid_payload_marker_is_not_returned_or_audited(self):
        marker = "distinctive-ingest-marker-9f4d0b"
        response = self.admin_client.post(
            "/api/ingest",
            json={"events": [{"user": "ingest-test-user", "files_accessed": marker}]},
            headers={"X-CSRF-Token": self._csrf(self.admin_client)},
        )
        self.assertEqual(response.status_code, 400)
        self.assertNotIn(marker, response.get_data(as_text=True))

        conn = database.connect()
        try:
            details = conn.execute(
                "SELECT detail FROM audit_log WHERE action = 'INGEST_FAILED'"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(len(details), 1)
        self.assertNotIn(marker, details[0][0])
        self.assertRegex(details[0][0], r"^validation_failed;exception=[A-Za-z]+$")

    def test_valid_event_is_inserted_and_succeeds(self):
        response = self.admin_client.post(
            "/api/ingest",
            json={
                "events": [{
                    "user": "ingest-test-user",
                    "role": "researcher",
                    "login_hour": 10,
                    "files_accessed": 2,
                    "data_transferred_mb": 1.5,
                    "failed_logins": 0,
                    "off_hours_access": 0,
                }]
            },
            headers={"X-CSRF-Token": self._csrf(self.admin_client)},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok", "inserted": 1})
        conn = database.connect()
        try:
            inserted = conn.execute(
                "SELECT COUNT(*) FROM logs WHERE user = ? AND source = 'push'",
                ("ingest-test-user",),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(inserted, 1)

    def test_missing_csrf_still_returns_400(self):
        response = self.admin_client.post(
            "/api/ingest",
            json={"events": []},
        )
        self.assertEqual(response.status_code, 400)

    def test_non_admin_still_receives_403(self):
        create_test_user(
            "ingest-manager",
            "ingest-manager-test-password",
            "management",
        )
        client = app.test_client()
        self._login(client, "ingest-manager", "ingest-manager-test-password")
        response = client.post(
            "/api/ingest",
            json={"events": []},
            headers={"X-CSRF-Token": self._csrf(client)},
        )
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
