import json
import os
import unittest

import database
from auth import init_users_table
from generate_logs import generate
from ingestion import ensure_logs_schema
from test_support import isolate_database, temporary_database_path

isolate_database()

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("INSIGHTER_DISABLE_BACKGROUND_SIM", "1")
os.environ.setdefault("INSIGHTER_LLM_WARMUP", "0")

from app import app


class SensorBackendIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.previous_database_path = database.DB_PATH
        self.database_path = temporary_database_path()
        database.DB_PATH = self.database_path
        database.ensure_database()
        generate()
        init_users_table()
        ensure_logs_schema()
        self.previous_token = os.environ.get("INSIGHTER_SENSOR_API_TOKEN")
        os.environ["INSIGHTER_SENSOR_API_TOKEN"] = (
            "integration-test-token-with-at-least-thirty-two-characters"
        )
        self.client = app.test_client()

    def tearDown(self):
        database.DB_PATH = self.previous_database_path
        if self.previous_token is None:
            os.environ.pop("INSIGHTER_SENSOR_API_TOKEN", None)
        else:
            os.environ["INSIGHTER_SENSOR_API_TOKEN"] = self.previous_token
        if os.path.exists(self.database_path):
            os.remove(self.database_path)

    def _post_sensor_event(
        self,
        event,
        token="integration-test-token-with-at-least-thirty-two-characters",
    ):
        return self.client.post(
            "/api/ingest",
            json={"events": [event]},
            headers={"Authorization": f"Bearer {token}"},
        )

    def test_sensor_payload_maps_to_model_fields_and_persists(self):
        event = {
            "event_id": "sensor-event-001",
            "username": "operator",
            "user": "ENDPOINT-01\\operator",
            "endpoint_id": "ENDPOINT-01",
            "role": "researcher",
            "category": "projects",
            "source": "endpoint_sensor",
            "timestamp": "2026-11-04T10:00:00Z",
            "login_hour": 10,
            "files_accessed": 0,
            "mb_transferred": 1.25,
            "failed_logins": 0,
            "off_hours": 0,
            "telemetry_status": {
                "network": "machine_wide_delta",
                "files_accessed": "unsupported_no_file_audit",
            },
        }

        response = self._post_sensor_event(event)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok", "inserted": 1})
        conn = database.connect()
        try:
            row = conn.execute(
                """SELECT user, role, source, data_category, data_transferred_mb,
                          files_accessed, failed_logins, off_hours_access,
                          payload_encrypted, feature_status
                   FROM logs WHERE user = ?""",
                ("ENDPOINT-01\\operator",),
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(
            row[1:8],
            ("researcher", "endpoint_sensor", "projects", 1.25, None, None, 0),
        )
        self.assertTrue(row[8])
        self.assertNotIn("ENDPOINT-01", row[8])
        self.assertEqual(
            json.loads(row[9]),
            {
                "login_hour": "supported",
                "files_accessed": "unsupported",
                "data_transferred_mb": "machine_wide",
                "failed_logins": "unsupported",
                "off_hours_access": "supported",
            },
        )
        from model import score_users

        self.assertNotIn(
            "ENDPOINT-01\\operator",
            [scored_user["user"] for scored_user in score_users()],
        )

    def test_unavailable_sensor_values_remain_null_and_are_not_scored(self):
        event = {
            "event_id": "sensor-event-no-network",
            "user": "ENDPOINT-02\\operator",
            "role": "researcher",
            "category": "projects",
            "login_hour": 10,
            "mb_transferred": 4.5,
            "off_hours": 0,
            "telemetry_status": {"network": "unsupported"},
        }
        response = self._post_sensor_event(event)

        self.assertEqual(response.status_code, 200)
        conn = database.connect()
        try:
            row = conn.execute(
                """SELECT data_transferred_mb, files_accessed, failed_logins,
                          feature_status FROM logs WHERE user = ?""",
                ("ENDPOINT-02\\operator",),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row[0:3], (None, None, None))
        statuses = json.loads(row[3])
        self.assertEqual(statuses["data_transferred_mb"], "unsupported")

    def test_legacy_sensor_rows_receive_conservative_quality_status(self):
        conn = database.connect()
        try:
            conn.execute(
                """INSERT INTO logs
                   (user, login_hour, files_accessed, data_transferred_mb,
                    failed_logins, off_hours_access, role, source, data_category,
                    feature_status)
                   VALUES (?, 10, 0, 1.0, 0, 0, 'researcher',
                           'endpoint_sensor', 'projects', NULL)""",
                ("LEGACY-ENDPOINT\\operator",),
            )
            conn.commit()
        finally:
            conn.close()

        ensure_logs_schema()

        conn = database.connect()
        try:
            status_json = conn.execute(
                "SELECT feature_status FROM logs WHERE user = ?",
                ("LEGACY-ENDPOINT\\operator",),
            ).fetchone()[0]
        finally:
            conn.close()
        statuses = json.loads(status_json)
        self.assertEqual(statuses["files_accessed"], "unsupported")
        self.assertEqual(statuses["failed_logins"], "unsupported")
        self.assertEqual(statuses["data_transferred_mb"], "machine_wide")

    def test_repeated_event_id_is_acknowledged_without_duplicate_row(self):
        event = {
            "event_id": "sensor-event-replay",
            "username": "operator",
            "role": "researcher",
            "category": "projects",
            "files_accessed": 0,
            "mb_transferred": 0,
            "failed_logins": 0,
            "off_hours": 0,
        }
        first = self._post_sensor_event(event)
        second = self._post_sensor_event(event)
        self.assertEqual(first.get_json()["inserted"], 1)
        self.assertEqual(second.get_json()["inserted"], 0)
        conn = database.connect()
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM logs WHERE user = ?",
                ("operator",),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 1)

    def test_sensor_requires_valid_service_token(self):
        response = self._post_sensor_event({"username": "operator"}, token="wrong-token")
        self.assertEqual(response.status_code, 401)

    def test_sensor_rejects_role_outside_active_sector(self):
        response = self._post_sensor_event({
            "event_id": "wrong-role",
            "username": "operator",
            "role": "drone_operator",
            "category": "projects",
            "files_accessed": 0,
            "mb_transferred": 0,
            "failed_logins": 0,
            "off_hours": 0,
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json(), {"error": "Invalid event data."})


if __name__ == "__main__":
    unittest.main()
