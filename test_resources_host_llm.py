import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("INSIGHTER_DISABLE_BACKGROUND_SIM", "1")
os.environ.setdefault("INSIGHTER_LLM_WARMUP", "0")

from PyQt6.QtWidgets import QApplication

from test_support import create_test_user, isolate_database, temporary_database_path

isolate_database()

import app as app_module
import database
import llm_advisor
from auth import init_users_table
from desktop_app.resources_tab import ResourcesTab


HOST_RESOURCE_FIELDS = {
    "proc_cpu",
    "proc_cpu_raw",
    "ram_used_mb",
    "ram_total_mb",
    "ram_percent",
    "threads",
    "disk_percent",
    "uptime_s",
    "ml_inference_ms",
    "db_read_ms",
    "db_write_ms",
    "last_tick_ms",
    "events_total",
    "events_per_sec",
}


class ResourcesHostAndLlmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.previous_database_path = database.DB_PATH
        self.database_path = temporary_database_path()
        database.DB_PATH = self.database_path
        database.ensure_database()
        init_users_table()
        create_test_user("resources-admin", "resources-admin-test-password", "admin")
        create_test_user(
            "resources-manager", "resources-manager-test-password", "management"
        )
        self.admin = self._login("resources-admin", "resources-admin-test-password")
        self.manager = self._login(
            "resources-manager", "resources-manager-test-password"
        )

    def tearDown(self):
        database.DB_PATH = self.previous_database_path
        for path in (self.database_path, self.database_path + ".usage"):
            if os.path.exists(path):
                os.remove(path)

    def _login(self, username, password):
        client = app_module.app.test_client()
        csrf = client.get("/api/csrf-token").get_json()["csrf_token"]
        response = client.post(
            "/login",
            data={
                "username": username,
                "password": password,
                "_csrf_token": csrf,
            },
        )
        self.assertEqual(response.status_code, 302)
        return client

    @staticmethod
    def _reachable_response():
        response = Mock()
        response.status_code = 200
        response.raise_for_status.return_value = None
        return response

    def test_llm_stats_endpoint_has_safe_bounded_shape(self):
        recorder = getattr(llm_advisor, "_record_llm_call", None)
        self.assertTrue(callable(recorder))
        reset = getattr(llm_advisor, "_clear_llm_stats_for_tests", None)
        self.assertTrue(callable(reset))
        reset()
        for index in range(105):
            recorder(
                model="phi4-mini",
                wall_latency_ms=float(index),
                total_duration_ms=1000.0,
                load_duration_ms=10.0,
                prompt_eval_duration_ms=20.0,
                eval_duration_ms=500.0,
                prompt_eval_count=50,
                eval_count=25,
                status="error" if index == 104 else "ok",
                error_type="Timeout" if index == 104 else None,
            )

        with patch.object(
            llm_advisor.requests, "get", return_value=self._reachable_response()
        ):
            response = self.admin.get("/api/llm-stats")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["model"], llm_advisor.OLLAMA_MODEL)
        self.assertEqual(payload["call_count"], 105)
        self.assertEqual(payload["retained_calls"], 100)
        self.assertEqual(len(payload["recent_calls"]), 100)
        self.assertEqual(payload["error_count"], 1)
        self.assertEqual(payload["timeout_count"], 1)
        self.assertAlmostEqual(payload["last_latency_ms"], 104.0)
        self.assertAlmostEqual(payload["rolling_average_ms"], 54.5)
        self.assertAlmostEqual(payload["p95_latency_ms"], 99.0)
        self.assertAlmostEqual(payload["tokens_per_second"], 50.0)
        self.assertTrue(payload["ollama_reachable"])
        self.assertEqual(
            set(payload["recent_calls"][-1]),
            {
                "timestamp",
                "model",
                "wall_latency_ms",
                "total_duration_ms",
                "load_duration_ms",
                "prompt_eval_duration_ms",
                "eval_duration_ms",
                "prompt_eval_count",
                "eval_count",
                "tokens_per_second",
                "status",
                "error_type",
            },
        )
        serialized = response.get_data(as_text=True)
        for identifier in (
            "resources-admin",
            "resources-manager",
            "alert_id",
            "PRIVATE-PROMPT-MARKER",
        ):
            self.assertNotIn(identifier, serialized)

    def test_llm_host_display_strips_credentials_path_and_query(self):
        with patch.object(
            llm_advisor,
            "OLLAMA_HOST",
            "https://user:PRIVATE-TOKEN@example.test:9443/private?token=PRIVATE-TOKEN",
        ):
            host, scope = llm_advisor._safe_host_details()

        self.assertEqual(host, "https://example.test:9443")
        self.assertEqual(scope, "remote")
        self.assertNotIn("PRIVATE-TOKEN", host)
        self.assertNotIn("user", host)

    def test_llm_stats_endpoint_returns_shape(self):
        reset = getattr(llm_advisor, "_clear_llm_stats_for_tests", None)
        if callable(reset):
            reset()
        with patch.object(
            llm_advisor.requests, "get", return_value=self._reachable_response()
        ):
            response = self.admin.get("/api/llm-stats")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["retained_calls"], 0)
        self.assertEqual(payload["recent_calls"], [])
        self.assertIn("ollama_reachable", payload)
        self.assertIn("warmup_state", payload)

    def test_llm_stats_endpoint_is_admin_only(self):
        response = self.manager.get("/api/llm-stats")
        self.assertEqual(response.status_code, 403)

    def test_closed_ollama_host_reports_absent_without_generation(self):
        with (
            patch.object(llm_advisor, "OLLAMA_HOST", "http://127.0.0.1:1"),
            patch.object(llm_advisor.requests, "post") as generate,
        ):
            response = self.admin.get("/api/llm-stats")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["ollama_reachable"])
        generate.assert_not_called()
        tab = ResourcesTab(object())
        self.addCleanup(tab.close)
        tab._apply_llm_stats(response.get_json())
        self.assertIn("Ollama not running", tab.llm_reachability_label.text())

    def test_stats_recording_omits_alert_identifiers_and_usernames(self):
        reset = getattr(llm_advisor, "_clear_llm_stats_for_tests", None)
        self.assertTrue(callable(reset))
        reset()
        alert = {
            "id": "PRIVATE-ALERT-MARKER",
            "user": "PRIVATE-USERNAME-MARKER",
            "role": "researcher",
            "login_hour": 10,
            "off_hours_access": 0,
            "data_category": "projects",
            "files_accessed": 1,
            "data_transferred_mb": 1.0,
            "failed_logins": 0,
            "description": "PRIVATE-ALERT-TEXT-MARKER",
            "severity": "MEDIUM",
            "flagged_by": "IF",
            "if_score": 50.0,
            "ocsvm_score": 50.0,
            "final_score": 50.0,
        }
        response = Mock()
        response.status_code = 200
        response.ok = True
        response.json.return_value = {
            "response": (
                '{"explanation":"The alert severity is MEDIUM and the score is 50.",'
                '"recommendation":"Review the alert fields."}'
            ),
            "prompt_eval_count": 40,
            "eval_count": 10,
            "total_duration": 1_000_000_000,
            "load_duration": 10_000_000,
            "prompt_eval_duration": 20_000_000,
            "eval_duration": 500_000_000,
        }
        with (
            patch.object(llm_advisor, "USAGE_LOG_PATH", self.database_path + ".usage"),
            patch.object(llm_advisor.requests, "post", return_value=response),
            patch.object(
                llm_advisor.requests, "get", return_value=self._reachable_response()
            ),
        ):
            result = llm_advisor.explain_alert(
                alert, "private_school", {"data_categories": ["projects"]}
            )
            payload = self.admin.get("/api/llm-stats").get_json()

        self.assertTrue(result["available"])
        self.assertEqual(payload["call_count"], 1)
        self.assertEqual(payload["recent_calls"][0]["status"], "ok")
        self.assertEqual(payload["recent_calls"][0]["tokens_per_second"], 20.0)
        serialized = str(payload)
        self.assertNotIn("PRIVATE-ALERT-MARKER", serialized)
        self.assertNotIn("PRIVATE-USERNAME-MARKER", serialized)
        self.assertNotIn("PRIVATE-ALERT-TEXT-MARKER", serialized)

    def test_resources_tab_renders_all_host_fields_and_llm_no_data_state(self):
        tab = ResourcesTab(object())
        self.addCleanup(tab.close)
        self.assertEqual(set(tab.metric_boxes), HOST_RESOURCE_FIELDS)
        host_response = self.admin.get("/api/resources")
        self.assertEqual(host_response.status_code, 200)
        self.assertEqual(set(host_response.get_json()), HOST_RESOURCE_FIELDS)
        host_metrics = {
            key: index + 1
            for index, key in enumerate(sorted(HOST_RESOURCE_FIELDS))
        }
        tab._apply_host_metrics(host_metrics)
        self.assertTrue(
            all(
                box.value_label.text() != "—"
                for box in tab.metric_boxes.values()
            )
        )
        tab._apply_llm_stats({
            "model": "phi4-mini",
            "ollama_host": "http://localhost:11434",
            "host_scope": "loopback",
            "ollama_reachable": True,
            "last_check_at": "2026-10-28T12:00:00+00:00",
            "warmup_state": "ready",
            "call_count": 0,
            "retained_calls": 0,
            "last_latency_ms": None,
            "rolling_average_ms": None,
            "p95_latency_ms": None,
            "error_count": 0,
            "timeout_count": 0,
            "tokens_per_second": None,
            "recent_calls": [],
            "benchmark": [],
        })
        self.assertIn("no data yet", tab.llm_data_label.text().casefold())
        self.assertIn("Loopback", tab.llm_host_label.text())
        self.assertIn("phi4-mini", tab.llm_model_label.text())
        self.assertIn("Benchmark (historical, not live)", tab.benchmark_label.text())

    def test_resources_tab_shows_ollama_absent_and_live_call_data(self):
        tab = ResourcesTab(object())
        self.addCleanup(tab.close)
        tab._apply_llm_stats({
            "model": "phi4-mini",
            "ollama_host": "http://127.0.0.1:11434",
            "host_scope": "loopback",
            "ollama_reachable": False,
            "last_check_at": "2026-10-28T12:00:00+00:00",
            "warmup_state": "failed",
            "call_count": 1,
            "retained_calls": 1,
            "last_latency_ms": 1250.0,
            "rolling_average_ms": 1250.0,
            "p95_latency_ms": 1250.0,
            "error_count": 1,
            "timeout_count": 1,
            "tokens_per_second": None,
            "recent_calls": [{
                "timestamp": "2026-10-28T12:00:00+00:00",
                "model": "phi4-mini",
                "wall_latency_ms": 1250.0,
                "status": "error",
                "error_type": "Timeout",
            }],
            "benchmark": [],
        })
        self.assertIn("Ollama not running", tab.llm_reachability_label.text())
        self.assertIn("1250.0", tab.llm_last_latency_label.text())
        self.assertEqual(tab.recent_calls_table.rowCount(), 1)
        self.assertIn("Timeout", tab.recent_calls_table.item(0, 3).text())

    def test_resources_tab_refresh_runs_off_the_ui_thread(self):
        class SlowClient:
            def get_resources(self):
                return {
                    "proc_cpu": 1.0,
                    "proc_cpu_raw": 1.0,
                    "ram_used_mb": 10.0,
                    "ram_total_mb": 100.0,
                    "ram_percent": 10.0,
                    "threads": 2,
                    "disk_percent": 20.0,
                    "uptime_s": 60,
                    "ml_inference_ms": 1.0,
                    "db_read_ms": 1.0,
                    "db_write_ms": 1.0,
                    "last_tick_ms": 3.0,
                    "events_total": 1,
                    "events_per_sec": 0.1,
                }

            def get_llm_stats(self):
                import time

                time.sleep(0.1)
                return {
                    "model": "phi4-mini",
                    "ollama_host": "http://localhost:11434",
                    "host_scope": "loopback",
                    "ollama_reachable": False,
                    "last_check_at": None,
                    "warmup_state": "disabled",
                    "call_count": 0,
                    "retained_calls": 0,
                    "last_latency_ms": None,
                    "rolling_average_ms": None,
                    "p95_latency_ms": None,
                    "error_count": 0,
                    "timeout_count": 0,
                    "tokens_per_second": None,
                    "recent_calls": [],
                    "benchmark": [],
                }

        import time

        tab = ResourcesTab(SlowClient())
        self.addCleanup(tab.close)
        started = time.perf_counter()
        tab.refresh()
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 0.05)
        from PyQt6.QtCore import QEventLoop, QTimer

        loop = QEventLoop()
        QTimer.singleShot(1000, loop.quit)
        loop.exec()


if __name__ == "__main__":
    unittest.main()
