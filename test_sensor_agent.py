import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

import sensor_agent


class SensorConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.previous_token = os.environ.get("TEST_SENSOR_TOKEN")
        os.environ["TEST_SENSOR_TOKEN"] = "test-token-with-at-least-thirty-two-characters"
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temp_dir.name) / "config.json"
        self.config = {
            "backend": {
                "host": "127.0.0.1",
                "port": 5051,
                "scheme": "http",
                "path": "/api/ingest",
                "timeout_seconds": 5,
            },
            "collection": {
                "interval_seconds": 30,
                "sampling_window_seconds": 5,
            },
            "endpoint": {"role": "researcher", "category": "projects"},
            "retry": {
                "initial_seconds": 2,
                "max_seconds": 60,
                "queue_max_events": 3,
            },
            "auth": {"token_env": "TEST_SENSOR_TOKEN"},
            "tls": {"verify": True},
        }

    def tearDown(self):
        if self.previous_token is None:
            os.environ.pop("TEST_SENSOR_TOKEN", None)
        else:
            os.environ["TEST_SENSOR_TOKEN"] = self.previous_token
        self.temp_dir.cleanup()

    def write_config(self):
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")

    def test_loads_external_config_without_exposing_token(self):
        self.write_config()
        loaded = sensor_agent.load_config(self.config_path)
        self.assertEqual(loaded["backend"]["host"], "127.0.0.1")
        self.assertEqual(loaded["endpoint"]["role"], "researcher")
        self.assertEqual(
            loaded["auth"]["token"],
            "test-token-with-at-least-thirty-two-characters",
        )

    def test_rejects_placeholder_backend_host(self):
        self.config["backend"]["host"] = "CHANGE_ME"
        self.write_config()
        with self.assertRaisesRegex(sensor_agent.ConfigurationError, "backend.host"):
            sensor_agent.load_config(self.config_path)

    def test_rejects_malformed_backend_host(self):
        self.config["backend"]["host"] = "admin pc:5051"
        self.write_config()
        with self.assertRaisesRegex(sensor_agent.ConfigurationError, "backend.host"):
            sensor_agent.load_config(self.config_path)

    def test_rejects_sample_window_longer_than_poll_interval(self):
        self.config["collection"]["sampling_window_seconds"] = 31
        self.write_config()
        with self.assertRaisesRegex(sensor_agent.ConfigurationError, "must not exceed"):
            sensor_agent.load_config(self.config_path)

    def test_rejects_missing_auth_token(self):
        os.environ.pop("TEST_SENSOR_TOKEN")
        self.write_config()
        with self.assertRaisesRegex(sensor_agent.ConfigurationError, "TEST_SENSOR_TOKEN"):
            sensor_agent.load_config(self.config_path)


class SensorTelemetryTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "collection": {"sampling_window_seconds": 2},
            "endpoint": {"role": "researcher", "category": "projects"},
        }

    def test_counter_delta_handles_increase_reset_and_unavailable(self):
        self.assertEqual(sensor_agent.safe_counter_delta(10, 25), (15, "available"))
        self.assertEqual(sensor_agent.safe_counter_delta(25, 10), (0, "counter_reset"))
        self.assertEqual(sensor_agent.safe_counter_delta(None, 10), (None, "unsupported"))

    def test_collect_event_labels_machine_wide_metrics_and_unsupported_fields(self):
        with (
            patch.object(sensor_agent, "_network_counters", side_effect=[
                {"bytes_sent": 1000, "bytes_recv": 2000},
                {"bytes_sent": 2000, "bytes_recv": 4000},
            ]),
            patch.object(sensor_agent, "_disk_counters", side_effect=[
                {"read_bytes": 100, "write_bytes": 200},
                {"read_bytes": 300, "write_bytes": 500},
            ]),
            patch.object(sensor_agent, "endpoint_identity", return_value={
                "user": "HOST\\operator",
                "username": "operator",
                "endpoint_id": "HOST",
                "hostname": "HOST",
                "os_info": "Windows",
                "role": "researcher",
                "category": "projects",
                "sensor_version": sensor_agent.SENSOR_VERSION,
            }),
        ):
            event = sensor_agent.collect_event(self.config, sleep=lambda _seconds: None)

        self.assertIsNone(event["files_accessed"])
        self.assertIsNone(event["failed_logins"])
        self.assertEqual(event["mb_transferred"], round(3000 / (1024 * 1024), 6))
        self.assertEqual(event["telemetry_status"]["network"], "machine_wide_delta")
        self.assertEqual(
            event["feature_status"]["data_transferred_mb"],
            "machine_wide",
        )
        self.assertEqual(
            event["telemetry_status"]["files_accessed"],
            "unsupported_no_file_audit",
        )
        self.assertEqual(event["telemetry_metrics"]["disk_read_bytes"], 200)
        self.assertTrue(event["timestamp"].endswith("Z"))
        self.assertTrue(event["event_id"])

    def test_unavailable_network_measurement_is_null_and_not_supported(self):
        with (
            patch.object(sensor_agent, "_network_counters", return_value=None),
            patch.object(sensor_agent, "_disk_counters", return_value=None),
            patch.object(sensor_agent, "endpoint_identity", return_value={
                "user": "HOST\\operator",
                "username": "operator",
                "endpoint_id": "HOST",
                "hostname": "HOST",
                "os_info": "Windows",
                "role": "researcher",
                "category": "projects",
                "sensor_version": sensor_agent.SENSOR_VERSION,
            }),
        ):
            event = sensor_agent.collect_event(self.config, sleep=lambda _seconds: None)

        self.assertIsNone(event["mb_transferred"])
        self.assertEqual(
            event["feature_status"]["data_transferred_mb"],
            "unsupported",
        )

    def test_endpoint_identity_qualifies_username_with_hostname(self):
        with (
            patch.object(sensor_agent.getpass, "getuser", return_value="operator"),
            patch.object(sensor_agent.socket, "gethostname", return_value="HOST-01"),
        ):
            identity = sensor_agent.endpoint_identity("researcher", "projects")
        self.assertEqual(identity["username"], "operator")
        self.assertEqual(identity["user"], "HOST-01\\operator")


class SensorTransportTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "backend": {
                "host": "localhost",
                "port": 5051,
                "scheme": "https",
                "path": "/api/ingest",
                "timeout_seconds": 4,
            },
            "auth": {"token": "test-token"},
            "tls": {"verify": True},
            "retry": {
                "initial_seconds": 2,
                "max_seconds": 8,
                "queue_max_events": 2,
            },
        }

    def test_posts_authenticated_payload_and_validates_ack(self):
        response = Mock(status_code=200)
        response.json.return_value = {"status": "ok", "inserted": 1}
        client = Mock()
        client.post.return_value = response
        event = {"event_id": "event-1"}

        sensor_agent.post_event(event, self.config, client)

        args, kwargs = client.post.call_args
        self.assertEqual(args[0], "https://localhost:5051/api/ingest")
        self.assertEqual(kwargs["json"], {"events": [event]})
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-token")
        self.assertFalse(kwargs["allow_redirects"])

    def test_rejects_redirect_without_following_it(self):
        response = Mock(status_code=302)
        client = Mock()
        client.post.return_value = response
        with self.assertRaises(sensor_agent.PermanentIngestError):
            sensor_agent.post_event({"event_id": "event-1"}, self.config, client)

    def test_retries_temporary_http_rate_limit(self):
        response = Mock(status_code=429)
        client = Mock()
        client.post.return_value = response
        with self.assertRaises(requests.HTTPError):
            sensor_agent.post_event({"event_id": "event-1"}, self.config, client)

    def test_sender_keeps_event_after_temporary_failure_and_bounds_queue(self):
        sender = sensor_agent.EventSender(self.config)
        sender.enqueue({"event_id": "old"})
        sender.enqueue({"event_id": "new"})
        sender.enqueue({"event_id": "latest"})
        self.assertEqual([item["event_id"] for item in sender.pending], ["new", "latest"])

        with (
            patch.object(sensor_agent, "post_event", side_effect=requests.Timeout("offline")),
            patch.object(sensor_agent.time, "monotonic", side_effect=[10.0, 10.0]),
            patch.object(sensor_agent.LOGGER, "warning"),
        ):
            self.assertFalse(sender.send_if_due())
        self.assertEqual(sender.pending[0]["event_id"], "new")
        self.assertEqual(sender.retry_delay, 4)


class SensorShutdownTests(unittest.TestCase):
    def test_stop_file_requests_graceful_exit_and_is_removed(self):
        stop_event = sensor_agent.threading.Event()
        with tempfile.TemporaryDirectory() as directory:
            stop_file = Path(directory) / "insighter_sensor.stop"
            stop_file.touch()
            with patch.object(
                sensor_agent, "configuration_path", return_value=Path(directory) / "config.json"
            ):
                self.assertTrue(sensor_agent._check_file_shutdown(stop_event))
            self.assertTrue(stop_event.is_set())
            self.assertFalse(stop_file.exists())


if __name__ == "__main__":
    unittest.main()
