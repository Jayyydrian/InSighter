import os
import sys
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QLibraryInfo, QtMsgType, qInstallMessageHandler
from PyQt6.QtWidgets import QApplication

from desktop_app import async_utils
from desktop_app.api_client import ApiError
from desktop_app.connection_state import ConnectionMonitor
from desktop_app.main import InSighterApp
from desktop_app.main_window import MainWindow


ADMIN_PAGE_CLIENT_METHODS = {
    0: ("Overview", ("get_scores", "get_alerts", "get_resources")),
    1: ("User Sessions", ("get_sessions",)),
    2: ("User Profiles", ("get_scores",)),
    3: ("Role Behavior", ("get_scores", "get_active_taxonomy")),
    4: ("Alerts", ("get_alerts",)),
    5: ("Resources", ("get_resources",)),
    6: ("Audit Log", ("get_audit_log",)),
    7: ("Integrations", ("get_integrations",)),
    8: ("Baseline Setup", ("get_baseline_setup",)),
    9: ("UAV Configuration", ("get_uav_config", "get_monitoring_scope")),
}
ROLE_PAGE_CLIENT_METHODS = {
    "management": {"Summary Report": ("get_summary",)},
    "compliance": {
        "Compliance Audit": ("get_compliance_alerts", "get_compliance_audit_log"),
    },
}

_qt_prefix = QLibraryInfo.path(QLibraryInfo.LibraryPath.PrefixPath)
QT_FONT_WARNING = (
    f"QFontDatabase: Cannot find font directory {_qt_prefix}/lib/fonts.\n"
    "Note that Qt no longer ships fonts. Deploy some (from "
    "https://dejavu-fonts.github.io/ for example) or switch to fontconfig."
)

# These exact Qt warnings are harmless limitations of the offscreen platform.
QT_OFFSCREEN_ALLOWLIST = {
    "This plugin does not support propagateSizeHints()": (
        "The offscreen plugin cannot propagate platform window size hints.",
    ),
    "This plugin does not support raise()": (
        "The offscreen plugin cannot raise a native window.",
    ),
    QT_FONT_WARNING: (
        "Qt distributions may omit optional bundled fonts; the warning does not "
        "affect widget construction or refresh behavior.",
    ),
}


class StartupErrorRecorder:
    def __init__(self):
        self.hook_failures = []
        self.qt_warnings = []
        self.async_failures = []
        self.monitor_failures = []
        self.expected_failures = []
        self._original_sys_excepthook = None
        self._original_threading_excepthook = None
        self._original_unraisablehook = None
        self._original_qt_handler = None
        self._deliver_patch = None
        self._monitor_patch = None

    def install(self):
        self._original_sys_excepthook = sys.excepthook
        self._original_threading_excepthook = threading.excepthook
        self._original_unraisablehook = sys.unraisablehook
        sys.excepthook = lambda *args: self.hook_failures.append(
            ("sys.excepthook", args)
        )
        # PyQt6 sends exceptions from queued result callbacks to sys.excepthook;
        # confirmed by injecting a failure into ProfilesTab._apply_refresh.
        threading.excepthook = lambda args: self.hook_failures.append(
            ("threading.excepthook", args)
        )
        sys.unraisablehook = lambda args: self.hook_failures.append(
            ("sys.unraisablehook", args)
        )
        self._original_qt_handler = qInstallMessageHandler(self._qt_message)

        original_deliver = async_utils._ResultBridge.deliver

        def record_delivery(bridge, result, error):
            if error is not None:
                self.async_failures.append(
                    (bridge.monitor_source, error)
                )
            return original_deliver(bridge, result, error)

        self._deliver_patch = patch.object(
            async_utils._ResultBridge, "deliver", record_delivery
        )
        self._deliver_patch.start()

        original_record_failure = ConnectionMonitor.record_failure

        def record_monitor_failure(
            monitor, exc, counts_toward_offline=True, source="refresh"
        ):
            self.monitor_failures.append((source, exc))
            return original_record_failure(
                monitor, exc, counts_toward_offline, source
            )

        self._monitor_patch = patch.object(
            ConnectionMonitor, "record_failure", record_monitor_failure
        )
        self._monitor_patch.start()

    def _qt_message(self, message_type, _context, message):
        if message_type.value >= QtMsgType.QtWarningMsg.value:
            if message not in QT_OFFSCREEN_ALLOWLIST:
                self.qt_warnings.append((message_type.name, message))

    def expect_handled_failure(self, source, message):
        self.expected_failures.append((source, message))

    def acknowledge_handled_failure(self, testcase, source, message):
        async_matches = [
            entry for entry in self.async_failures
            if entry[0] == source and str(entry[1]) == message
        ]
        monitor_matches = [
            entry for entry in self.monitor_failures
            if entry[0] == source and str(entry[1]) == message
        ]
        testcase.assertEqual(
            len(async_matches), 1,
            f"Expected one run_async failure for {source}: {self.describe()}",
        )
        testcase.assertEqual(
            len(monitor_matches), 1,
            f"Expected one ConnectionMonitor failure for {source}: {self.describe()}",
        )
        self.async_failures.remove(async_matches[0])
        self.monitor_failures.remove(monitor_matches[0])
        self.expected_failures.remove((source, message))

    def describe(self):
        return (
            f"hooks={self.hook_failures!r}; qt_warnings={self.qt_warnings!r}; "
            f"async_failures={self.async_failures!r}; "
            f"monitor_failures={self.monitor_failures!r}; "
            f"expected_failures={self.expected_failures!r}"
        )

    def assert_empty(self, testcase):
        testcase.assertEqual(
            self.describe(),
            "hooks=[]; qt_warnings=[]; async_failures=[]; "
            "monitor_failures=[]; expected_failures=[]",
            f"Unexpected startup/async errors were recorded: {self.describe()}",
        )

    def uninstall(self):
        if self._monitor_patch is not None:
            self._monitor_patch.stop()
        if self._deliver_patch is not None:
            self._deliver_patch.stop()
        qInstallMessageHandler(self._original_qt_handler)
        sys.excepthook = self._original_sys_excepthook
        threading.excepthook = self._original_threading_excepthook
        sys.unraisablehook = self._original_unraisablehook


class StartupFakeClient:
    def __init__(self, role="admin"):
        self.role = role
        self.username = f"test-{role}"
        self.connection_monitor = None
        self.calls = []
        self.failures = {}
        self.config = {
            "sector": "private_school",
            "taxonomy": {
                "roles": ["researcher", "intern", "admin_staff", "it_staff"],
                "data_categories": [
                    "projects",
                    "private_documents",
                    "intellectual_property",
                    "pii",
                ],
            },
            "threshold_high": 45,
            "threshold_medium": 30,
            "sync_interval_minutes": 15,
            "drone_operator_username": "drone_operator",
            "log_targets": "active_directory",
            "updated_at": "test",
        }

    def _record_call(self, name, *args):
        self.calls.append((name, args))
        failure = self.failures.get(name)
        if failure is not None:
            raise failure

    def call_count(self, name):
        return sum(call_name == name for call_name, _args in self.calls)

    def get_scores(self):
        self._record_call("get_scores")
        return []

    def get_alerts(self):
        self._record_call("get_alerts")
        return []

    def get_resources(self):
        self._record_call("get_resources")
        return {
            "proc_cpu": 0,
            "proc_cpu_raw": 0,
            "ram_used_mb": 0,
            "ram_percent": 0,
            "ml_inference_ms": 0,
            "db_read_ms": 0,
            "db_write_ms": 0,
            "last_tick_ms": 0,
            "events_total": 0,
            "events_per_sec": 0,
            "threads": 1,
            "uptime_s": 0,
        }

    def get_sessions(self):
        self._record_call("get_sessions")
        return []

    def get_active_taxonomy(self):
        self._record_call("get_active_taxonomy")
        return self.config["taxonomy"]

    def get_deployment_taxonomy(self):
        self._record_call("get_deployment_taxonomy")
        return self.config["taxonomy"]

    def get_audit_log(self):
        self._record_call("get_audit_log")
        return []

    def get_integrations(self):
        self._record_call("get_integrations")
        return []

    def get_baseline_setup(self):
        self._record_call("get_baseline_setup")
        return {"sector": "private_school", "status": "configured", "roles": []}

    def get_uav_config(self):
        self._record_call("get_uav_config")
        return self.config.copy()

    def get_monitoring_scope(self):
        self._record_call("get_monitoring_scope")
        return {"in_scope_percent": 0, "in_scope_events": 0, "total_events": 0}

    def save_deployment_config(self, payload):
        self._record_call("save_deployment_config", payload.copy())
        self.config.update(payload)
        return self.config.copy()

    def save_uav_config(self, payload):
        self._record_call("save_uav_config", payload.copy())
        return self.save_deployment_config(payload)

    def explain_alert(self, alert_id):
        self._record_call("explain_alert", alert_id)
        return {
            "available": False,
            "reason": "AI analysis unavailable",
            "explanation": None,
            "recommendation": None,
        }

    def ingest_events(self):
        self._record_call("ingest_events")
        return {"inserted": 0}

    def save_baseline_setup(self, payload):
        self._record_call("save_baseline_setup", payload.copy())
        return {
            "status": "saved",
            "configured": payload.get("complete", False),
        }

    def reset_baseline_setup(self):
        self._record_call("reset_baseline_setup")
        return {"status": "reset"}

    def reseed(self):
        self._record_call("reseed")
        return {"status": "ok"}

    def get_summary(self):
        self._record_call("get_summary")
        return {"HIGH": 0, "MEDIUM": 0, "LOW": 0}

    def get_compliance_alerts(self):
        self._record_call("get_compliance_alerts")
        return []

    def get_compliance_audit_log(self):
        self._record_call("get_compliance_audit_log")
        return []

    def logout(self):
        self._record_call("logout")
        return True


class DesktopStartupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.recorder = StartupErrorRecorder()
        self.recorder.install()

    def tearDown(self):
        try:
            self.recorder.assert_empty(self)
        finally:
            self.recorder.uninstall()

    def wait_for_idle(self, timeout=5):
        deadline = time.monotonic() + timeout
        idle_passes = 0
        while time.monotonic() < deadline:
            self.app.processEvents()
            if async_utils._workers:
                idle_passes = 0
            else:
                idle_passes += 1
                if idle_passes >= 3:
                    self.app.processEvents()
                    return True
            time.sleep(0.01)
        self.app.processEvents()
        return not async_utils._workers

    def wait_until(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.01)
        self.app.processEvents()
        return predicate()

    def close_window(self, window):
        if window is None:
            return
        if hasattr(window, "timer"):
            window.timer.stop()
            window.clock_timer.stop()
        window.close()
        self.assertTrue(self.wait_for_idle())

    def assert_calls_increased(self, client, methods, previous_counts, page_name):
        for method in methods:
            self.assertGreater(
                client.call_count(method),
                previous_counts[method],
                f"{page_name} refresh did not call {method}; calls={client.calls!r}",
            )

    def test_admin_main_window_visits_every_page(self):
        client = StartupFakeClient("admin")
        window = MainWindow(client, on_logout=lambda: None)
        try:
            window.show()
            self.assertTrue(self.wait_for_idle(), self.recorder.describe())
            self.assertEqual(len(window._nav_pages), len(ADMIN_PAGE_CLIENT_METHODS))
            for page_index, (page_name, expected_methods) in (
                ADMIN_PAGE_CLIENT_METHODS.items()
            ):
                with self.subTest(page=page_name):
                    client.calls.clear()
                    nav_item = next(
                        item for item, index in window._nav_pages.items()
                        if index == page_index
                    )
                    nav_item.click()
                    self.app.processEvents()
                    self.assertEqual(window.stack.currentIndex(), page_index)
                    self.assertTrue(
                        self.wait_for_idle(), self.recorder.describe()
                    )
                    self.assert_calls_increased(
                        client,
                        expected_methods,
                        {method: 0 for method in expected_methods},
                        page_name,
                    )
        finally:
            self.close_window(window)

    def test_management_main_window_visits_every_page(self):
        self.assert_role_page("management")

    def test_compliance_main_window_visits_every_page(self):
        self.assert_role_page("compliance")

    def assert_role_page(self, role):
        client = StartupFakeClient(role)
        window = MainWindow(client, on_logout=lambda: None)
        try:
            window.show()
            self.assertTrue(self.wait_for_idle(), self.recorder.describe())
            client.calls.clear()
            if role == "management":
                self.assertEqual(len(window.tab_refs), 1)
            else:
                self.assertIsNotNone(window.compliance_tab)
            window._refresh_active()
            self.assertTrue(self.wait_for_idle(), self.recorder.describe())
            page_name, expected_methods = next(
                iter(ROLE_PAGE_CLIENT_METHODS[role].items())
            )
            self.assert_calls_increased(
                client,
                expected_methods,
                {method: 0 for method in expected_methods},
                page_name,
            )
        finally:
            self.close_window(window)

    def test_worker_request_failure_is_recorded(self):
        client = StartupFakeClient("admin")
        client.failures["get_sessions"] = RuntimeError(
            "injected session request failure"
        )
        window = MainWindow(client, on_logout=lambda: None)
        try:
            window.show()
            self.assertTrue(self.wait_for_idle(), self.recorder.describe())
            sessions_page = next(
                item for item, index in window._nav_pages.items() if index == 1
            )
            sessions_page.click()
            self.assertTrue(self.wait_for_idle(), self.recorder.describe())

            expected_failure = (
                "SessionsTab",
                "injected session request failure",
            )
            self.recorder.expect_handled_failure(*expected_failure)
            self.recorder.acknowledge_handled_failure(
                self, *expected_failure
            )
        finally:
            self.close_window(window)

    def test_saving_thresholds_refreshes_dashboard(self):
        client = StartupFakeClient()
        window = MainWindow(client, on_logout=lambda: None)
        window.timer.stop()
        try:
            window.show()
            self.assertTrue(self.wait_for_idle(), self.recorder.describe())
            initial_score_calls = client.call_count("get_scores")
            window.uav_tab.threshold_high.setValue(46)
            window.uav_tab.save_btn.click()

            self.assertTrue(
                self.wait_until(
                    lambda: window.uav_tab.status_label.text()
                    == "Configuration saved."
                    and window.uav_tab.save_btn.isEnabled(),
                ),
                self.recorder.describe(),
            )
            self.assertTrue(self.wait_for_idle(), self.recorder.describe())
            self.assertEqual(client.config["threshold_high"], 46)
            # The stopped timer makes this one signal-driven fetch deterministic;
            # the dashboard in-flight guard suppresses any duplicate refresh.
            self.assertEqual(
                client.call_count("get_scores"),
                initial_score_calls + 1,
                "A successful threshold save must trigger exactly one dashboard "
                "score fetch through configuration_saved.",
            )
        finally:
            self.close_window(window)

    def test_failed_threshold_save_does_not_refresh_dashboard(self):
        error_message = "intentional deployment save failure"
        client = StartupFakeClient()
        client.failures["save_deployment_config"] = ApiError(400, error_message)
        window = MainWindow(client, on_logout=lambda: None)
        try:
            window.show()
            self.assertTrue(self.wait_for_idle(), self.recorder.describe())
            initial_score_calls = client.call_count("get_scores")
            window.uav_tab.threshold_high.setValue(46)
            self.recorder.expect_handled_failure("uav-config-save", error_message)
            window.uav_tab.save_btn.click()

            self.assertTrue(
                self.wait_until(
                    lambda: window.uav_tab.status_label.text().startswith(
                        "Failed to save:"
                    )
                    and window.uav_tab.save_btn.isEnabled(),
                ),
                self.recorder.describe(),
            )
            self.assertTrue(self.wait_for_idle(), self.recorder.describe())
            self.assertEqual(client.call_count("get_scores"), initial_score_calls)
            self.recorder.acknowledge_handled_failure(
                self, "uav-config-save", error_message
            )
        finally:
            self.close_window(window)

    def test_logout_shows_login_and_second_main_window_starts(self):
        app_controller = InSighterApp.__new__(InSighterApp)
        app_controller.qapp = self.app
        app_controller.main_window = None
        app_controller.login_window = None
        first_window = None
        second_window = None
        try:
            app_controller._show_login()
            app_controller.login_window.show()
            app_controller._show_main_window(StartupFakeClient())
            first_window = app_controller.main_window
            first_window.show()
            self.assertTrue(self.wait_for_idle(), self.recorder.describe())

            first_window._logout()
            self.assertIsNotNone(app_controller.login_window)
            self.assertTrue(app_controller.login_window.isVisible())
            self.assertTrue(self.wait_for_idle(), self.recorder.describe())

            app_controller._show_main_window(StartupFakeClient())
            second_window = app_controller.main_window
            second_window.show()
            self.assertTrue(self.wait_for_idle(), self.recorder.describe())
            self.assertTrue(second_window.isVisible())
        finally:
            self.close_window(first_window)
            self.close_window(second_window)
            if app_controller.login_window is not None:
                app_controller.login_window.close()


if __name__ == "__main__":
    unittest.main()
