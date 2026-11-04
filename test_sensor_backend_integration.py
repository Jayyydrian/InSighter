import json
import os
import unittest

import database
from auth import init_users_table
from generate_logs import generate
from ingestion import ensure_logs_schema
from test_support import temporary_database_path

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
        self.assertEqual(self.client.get("/api/whoami").status_code, 302)
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

    def test_available_failed_login_zero_is_persisted_as_supported_not_missing(self):
        event = {
            "event_id": "sensor-failed-login-zero",
            "user": "ENDPOINT-03\\intern",
            "endpoint_id": "ENDPOINT-03",
            "role": "intern",
            "category": "private_documents",
            "login_hour": 10,
            "failed_logins": 0,
            "off_hours": 0,
            "telemetry_status": {
                "network": "machine_wide_delta",
                "failed_logins": "available",
            },
        }

        response = self._post_sensor_event(event)

        self.assertEqual(response.get_json(), {"status": "ok", "inserted": 1})
        conn = database.connect()
        try:
            row = conn.execute(
                """SELECT failed_logins, files_accessed, feature_status
                   FROM logs WHERE user = ?""",
                ("ENDPOINT-03\\intern",),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row[0:2], (0, None))
        statuses = json.loads(row[2])
        self.assertEqual(statuses["failed_logins"], "supported")
        self.assertEqual(statuses["files_accessed"], "unsupported")

    def test_failed_login_count_is_null_unless_collector_reports_available(self):
        event = {
            "event_id": "sensor-failed-login-unavailable",
            "user": "ENDPOINT-04\\intern",
            "role": "intern",
            "category": "private_documents",
            "login_hour": 10,
            "failed_logins": 4,
            "off_hours": 0,
            "telemetry_status": {"failed_logins": "unavailable"},
        }

        response = self._post_sensor_event(event)

        self.assertEqual(response.status_code, 200)
        conn = database.connect()
        try:
            row = conn.execute(
                """SELECT failed_logins, feature_status FROM logs WHERE user = ?""",
                ("ENDPOINT-04\\intern",),
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNone(row[0])
        self.assertEqual(json.loads(row[1])["failed_logins"], "unsupported")

    def test_invalid_available_failed_login_value_remains_null(self):
        event = {
            "event_id": "sensor-failed-login-invalid",
            "user": "ENDPOINT-05\\intern",
            "role": "intern",
            "category": "private_documents",
            "login_hour": 10,
            "failed_logins": True,
            "off_hours": 0,
            "telemetry_status": {"failed_logins": "available"},
        }

        response = self._post_sensor_event(event)

        self.assertEqual(response.status_code, 200)
        conn = database.connect()
        try:
            value = conn.execute(
                "SELECT failed_logins FROM logs WHERE user = ?",
                ("ENDPOINT-05\\intern",),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertIsNone(value)

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

    def test_endpoint_activity_uses_additive_table_and_stays_out_of_model_logs(self):
        event = {
            "event_id": "usb-connect-001",
            "source": "endpoint_sensor",
            "event_type": "usb_connected",
            "timestamp": "2026-11-05T00:00:00Z",
            "user": "ENDPOINT-USB\\operator",
            "username": "operator",
            "endpoint_id": "ENDPOINT-USB",
            "hostname": "ENDPOINT-USB",
            "role": "researcher",
            "category": "projects",
            "drive_letter": "E:\\",
            "sensor_version": "1.0.0",
        }
        conn = database.connect()
        try:
            original_log_count = conn.execute(
                "SELECT COUNT(*) FROM logs"
            ).fetchone()[0]
        finally:
            conn.close()

        first = self._post_sensor_event(event)
        replay = self._post_sensor_event(event)

        self.assertEqual(first.get_json(), {"status": "ok", "inserted": 1})
        self.assertEqual(replay.get_json(), {"status": "ok", "inserted": 0})
        conn = database.connect()
        try:
            activity = conn.execute(
                """SELECT event_type, drive_letter, operation_counts, payload_encrypted
                   FROM endpoint_activity_events WHERE event_id = ?""",
                ("usb-connect-001",),
            ).fetchone()
            log_count = conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(activity[:3], ("usb_connected", "E:\\", "{}"))
        self.assertTrue(activity[3])
        self.assertEqual(log_count, original_log_count)
        from model import score_users

        self.assertNotIn(
            "ENDPOINT-USB\\operator",
            [scored_user["user"] for scored_user in score_users()],
        )

    def test_filesystem_activity_persists_operation_counts_without_paths(self):
        event = {
            "event_id": "file-counts-001",
            "source": "endpoint_sensor",
            "event_type": "filesystem_activity",
            "timestamp": "2026-11-05T00:00:00Z",
            "user": "ENDPOINT-FILES\\operator",
            "endpoint_id": "ENDPOINT-FILES",
            "role": "researcher",
            "category": "projects",
            "drive_letter": "F:\\",
            "operation_counts": {
                "create": 2,
                "modify": 1,
                "delete": 1,
                "rename": 1,
            },
        }

        response = self._post_sensor_event(event)

        self.assertEqual(response.get_json(), {"status": "ok", "inserted": 1})
        conn = database.connect()
        try:
            row = conn.execute(
                """SELECT event_type, operation_counts, payload_encrypted
                   FROM endpoint_activity_events WHERE event_id = ?""",
                ("file-counts-001",),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row[0], "filesystem_activity")
        self.assertEqual(
            json.loads(row[1]),
            {"create": 2, "delete": 1, "modify": 1, "rename": 1},
        )
        self.assertTrue(row[2])

    def test_filesystem_activity_rejects_file_names_and_paths(self):
        event = {
            "event_id": "file-path-rejected",
            "source": "endpoint_sensor",
            "event_type": "filesystem_activity",
            "timestamp": "2026-11-05T00:00:00Z",
            "user": "ENDPOINT-FILES\\operator",
            "endpoint_id": "ENDPOINT-FILES",
            "role": "researcher",
            "category": "projects",
            "drive_letter": "F:\\",
            "operation_counts": {"modify": 1},
            "file_name": "secret.txt",
        }

        response = self._post_sensor_event(event)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json(), {"error": "Invalid event data."})
        conn = database.connect()
        try:
            table = conn.execute(
                """SELECT name FROM sqlite_master
                   WHERE type = 'table' AND name = 'endpoint_activity_events'"""
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNone(table)

    def test_endpoint_activity_rejects_data_category_outside_active_sector(self):
        response = self._post_sensor_event({
            "event_id": "usb-invalid-category",
            "source": "endpoint_sensor",
            "event_type": "usb_connected",
            "timestamp": "2026-11-05T00:00:00Z",
            "user": "ENDPOINT-USB\\operator",
            "endpoint_id": "ENDPOINT-USB",
            "role": "researcher",
            "category": "drone_platform_access",
            "drive_letter": "E:\\",
        })

        self.assertEqual(response.status_code, 400)

    def test_endpoint_activity_api_remains_admin_only(self):
        response = self.client.get("/api/endpoint-activity")

        self.assertEqual(response.status_code, 302)

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
