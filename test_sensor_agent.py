import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

import sensor_agent
from failed_login_monitor import FailedLoginObservation


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
        self.assertFalse(loaded["endpoint_activity"]["enabled"])
        self.assertFalse(loaded["failed_login_monitoring"]["enabled"])
        self.assertEqual(
            loaded["auth"]["token"],
            "test-token-with-at-least-thirty-two-characters",
        )

    def test_loads_endpoint_activity_settings(self):
        self.config["endpoint_activity"] = {
            "enabled": True,
            "poll_interval_seconds": 3,
        }
        self.write_config()

        loaded = sensor_agent.load_config(self.config_path)

        self.assertEqual(
            loaded["endpoint_activity"],
            {"enabled": True, "poll_interval_seconds": 3.0},
        )

    def test_rejects_invalid_endpoint_activity_settings(self):
        self.config["endpoint_activity"] = {
            "enabled": "yes",
            "poll_interval_seconds": 3,
        }
        self.write_config()

        with self.assertRaisesRegex(
            sensor_agent.ConfigurationError, "must be a boolean"
        ):
            sensor_agent.load_config(self.config_path)

    def test_loads_optional_failed_login_monitor_setting(self):
        self.config["failed_login_monitoring"] = {"enabled": True}
        self.write_config()

        loaded = sensor_agent.load_config(self.config_path)

        self.assertEqual(loaded["failed_login_monitoring"], {"enabled": True})

    def test_rejects_invalid_failed_login_monitor_setting(self):
        self.config["failed_login_monitoring"] = {"enabled": "yes"}
        self.write_config()

        with self.assertRaisesRegex(
            sensor_agent.ConfigurationError, "failed_login_monitoring.enabled"
        ):
            sensor_agent.load_config(self.config_path)

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
        self.assertEqual(event["telemetry_status"]["failed_logins"], "disabled")
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

    def test_available_failed_login_zero_is_measured_and_supported(self):
        monitor = Mock()
        monitor.observe.return_value = FailedLoginObservation(0, "available", 30.0)
        with (
            patch.object(sensor_agent, "_network_counters", side_effect=[
                {"bytes_sent": 1000, "bytes_recv": 2000},
                {"bytes_sent": 2000, "bytes_recv": 4000},
            ]),
            patch.object(sensor_agent, "_disk_counters", return_value=None),
            patch.object(sensor_agent, "endpoint_identity", return_value={
                "user": "HOST\\intern",
                "username": "intern",
                "endpoint_id": "HOST",
                "hostname": "HOST",
                "os_info": "Windows",
                "role": "intern",
                "category": "private_documents",
                "sensor_version": sensor_agent.SENSOR_VERSION,
            }),
        ):
            event = sensor_agent.collect_event(
                self.config,
                sleep=lambda _seconds: None,
                failed_login_monitor=monitor,
            )

        self.assertEqual(event["failed_logins"], 0)
        self.assertEqual(event["telemetry_status"]["failed_logins"], "available")
        self.assertEqual(event["feature_status"]["failed_logins"], "supported")
        self.assertEqual(
            event["telemetry_metrics"]["failed_logins_observation_window_seconds"],
            30.0,
        )

    def test_unavailable_failed_login_collection_keeps_sensor_event_valid(self):
        monitor = Mock()
        monitor.observe.return_value = FailedLoginObservation(None, "unavailable")
        with (
            patch.object(sensor_agent, "_network_counters", return_value=None),
            patch.object(sensor_agent, "_disk_counters", return_value=None),
            patch.object(sensor_agent, "endpoint_identity", return_value={
                "user": "HOST\\intern",
                "username": "intern",
                "endpoint_id": "HOST",
                "hostname": "HOST",
                "os_info": "Windows",
                "role": "intern",
                "category": "private_documents",
                "sensor_version": sensor_agent.SENSOR_VERSION,
            }),
        ):
            event = sensor_agent.collect_event(
                self.config,
                sleep=lambda _seconds: None,
                failed_login_monitor=monitor,
            )

        self.assertIsNone(event["failed_logins"])
        self.assertEqual(event["telemetry_status"]["failed_logins"], "unavailable")
        self.assertEqual(event["feature_status"]["failed_logins"], "unsupported")
        self.assertIsNone(event["mb_transferred"])

    def test_failed_login_collector_exception_does_not_discard_standard_telemetry(self):
        monitor = Mock()
        monitor.observe.side_effect = RuntimeError("event log unavailable")
        with (
            patch.object(sensor_agent, "_network_counters", return_value=None),
            patch.object(sensor_agent, "_disk_counters", return_value=None),
            patch.object(sensor_agent, "endpoint_identity", return_value={
                "user": "HOST\\intern",
                "username": "intern",
                "endpoint_id": "HOST",
                "hostname": "HOST",
                "os_info": "Windows",
                "role": "intern",
                "category": "private_documents",
                "sensor_version": sensor_agent.SENSOR_VERSION,
            }),
            patch.object(sensor_agent.LOGGER, "exception") as log_exception,
        ):
            event = sensor_agent.collect_event(
                self.config,
                sleep=lambda _seconds: None,
                failed_login_monitor=monitor,
            )

        self.assertEqual(event["telemetry_status"]["failed_logins"], "error")
        self.assertIsNone(event["failed_logins"])
        self.assertEqual(event["user"], "HOST\\intern")
        log_exception.assert_called_once()

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

    def test_run_stops_endpoint_monitor_worker_without_changing_sensor_cycle(self):
        stop_event = sensor_agent.threading.Event()
        activity_worker_started = sensor_agent.threading.Event()
        config = {
            "backend": {
                "host": "127.0.0.1",
                "port": 5051,
                "scheme": "http",
                "path": "/api/ingest",
                "timeout_seconds": 1,
            },
            "collection": {
                "interval_seconds": 5,
                "sampling_window_seconds": 1,
            },
            "endpoint": {"role": "researcher", "category": "projects"},
            "retry": {
                "initial_seconds": 1,
                "max_seconds": 2,
                "queue_max_events": 3,
            },
            "auth": {"token": "test-token"},
            "tls": {"verify": True},
            "endpoint_activity": {
                "enabled": True,
                "poll_interval_seconds": 1,
            },
        }

        def wait_for_stop(_config, _sensor_stop, worker_stop, _queue):
            activity_worker_started.set()
            worker_stop.wait(2)

        timer = sensor_agent.threading.Timer(0.1, stop_event.set)
        with (
            patch.object(sensor_agent, "_endpoint_activity_worker", wait_for_stop),
            patch.object(sensor_agent, "collect_event", return_value={"event_id": "base"}),
            patch.object(sensor_agent, "_check_file_shutdown", return_value=False),
            patch.object(sensor_agent.EventSender, "send_if_due", return_value=False),
        ):
            timer.start()
            sensor_agent.run(config, stop_event)
            timer.join()

        self.assertTrue(activity_worker_started.is_set())
        self.assertTrue(stop_event.is_set())


if __name__ == "__main__":
    unittest.main()
