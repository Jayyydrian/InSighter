import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QLabel

from desktop_app.api_client import ApiError
from desktop_app.alerts_tab import AlertsTab
from desktop_app import connection_state
from desktop_app.connection_state import CONNECTED, OFFLINE, ConnectionMonitor
from desktop_app.dashboard_tab import DashboardTab, risk_level_for, score_color
from desktop_app.safe_text import plain_label
from desktop_app import server_launcher


class DesktopSafetyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_untrusted_label_text_is_not_interpreted_as_rich_text(self):
        payload = "<img src=x onerror=alert(1)>"
        label = plain_label(payload)
        self.assertEqual(label.text(), payload)
        self.assertEqual(label.textFormat(), Qt.TextFormat.PlainText)
        self.assertFalse(label.openExternalLinks())

    def test_alert_data_is_rendered_as_plain_text(self):
        tab = AlertsTab(object())
        payload = "<b>untrusted</b>"
        card = tab._alert_card({
            "id": 1,
            "user": payload,
            "login_hour": 2,
            "description": payload,
            "flagged_by": payload,
            "final_score": 80,
            "severity": "HIGH",
        })
        matching_labels = [
            label for label in card.findChildren(QLabel)
            if label.text() == payload or payload in label.text()
        ]
        self.assertGreaterEqual(len(matching_labels), 3)
        self.assertTrue(all(
            label.textFormat() == Qt.TextFormat.PlainText
            for label in matching_labels
        ))

    def test_connection_monitor_transitions_and_expiry_is_emitted_once(self):
        monitor = ConnectionMonitor()
        states = []
        expiries = []
        monitor.state_changed.connect(lambda state, _message: states.append(state))
        monitor.session_expired.connect(lambda: expiries.append(True))

        monitor.record_failure(ApiError(None, "offline"), True, "test")
        monitor.record_failure(ApiError(503, "unavailable"), True, "test")
        self.assertEqual(monitor.state, OFFLINE)
        monitor.record_failure(ApiError(403, "forbidden"), True, "test")
        self.assertEqual(monitor.state, OFFLINE)
        monitor.record_failure(ApiError(401, "expired"), True, "test")
        monitor.record_failure(ApiError(401, "expired"), True, "test")
        self.app.processEvents()
        self.assertEqual(expiries, [True])
        monitor.record_success("test")
        self.assertEqual(monitor.state, CONNECTED)
        self.assertEqual(states, [OFFLINE, CONNECTED])

    def test_dashboard_risk_colors_follow_configured_thresholds(self):
        self.assertEqual(risk_level_for(70, 65, 40), "HIGH")
        self.assertEqual(risk_level_for(40, 65, 40), "MEDIUM")
        self.assertEqual(risk_level_for(39, 65, 40), "LOW")
        self.assertEqual(score_color(65, 65, 40), "#ef4444")
        self.assertEqual(score_color(40, 65, 40), "#f59e0b")
        self.assertEqual(score_color(39, 65, 40), "#10b981")

    def test_dashboard_counts_and_badges_keep_server_risk_level(self):
        tab = DashboardTab(object())
        tab.set_thresholds(70, 55)
        tab._refresh_scores([{
            "user": "operator",
            "role": "drone_operator",
            "risk_score": 60,
            "risk_level": "HIGH",
            "if_score": 55,
            "ocsvm_score": 58,
            "avg_files": 10,
            "avg_transfer": 2,
            "off_hours": 0,
            "total_logs": 10,
            "anomalous_logs": 2,
        }])

        self.assertEqual(tab.card_high.value_label.text(), "1")
        self.assertEqual(tab.card_medium.value_label.text(), "0")
        self.assertEqual(tab.card_low.value_label.text(), "0")
        badge_widget = tab.table.cellWidget(0, 4)
        self.assertEqual(badge_widget.findChild(QLabel).text(), "HIGH")
        score_widget = tab.table.cellWidget(0, 3)
        self.assertIn("#f59e0b", score_widget.findChildren(QLabel)[0].styleSheet())

    def test_connection_failure_logs_first_and_each_sixty_seconds_then_recovery(self):
        monitor = ConnectionMonitor()
        with patch.object(
            connection_state.time, "monotonic", side_effect=(0, 30, 60)
        ), self.assertLogs("desktop_app.connection_state", level="INFO") as captured:
            monitor.record_failure(ApiError(None, "offline"), True, "DashboardTab")
            monitor.record_failure(ApiError(None, "offline"), True, "DashboardTab")
            monitor.record_failure(ApiError(None, "offline"), True, "DashboardTab")
            monitor.record_success("DashboardTab")

        warnings = [line for line in captured.output if "WARNING" in line]
        recoveries = [line for line in captured.output if "INFO" in line]
        self.assertEqual(len(warnings), 2)
        self.assertEqual(len(recoveries), 1)
        self.assertIn("Connection recovered", recoveries[0])

    def test_unknown_or_unrelated_port_listener_is_never_terminated(self):
        unknown = server_launcher.Listener(None, False, None, None, True)
        unrelated = server_launcher.Listener(321, False, None, "python.exe", False)
        for listener in (unknown, unrelated):
            with self.subTest(listener=listener), \
                    patch.object(server_launcher, "_identify_listener", return_value=listener), \
                    patch.object(server_launcher, "_terminate_listener") as terminate, \
                    patch.object(server_launcher, "_start_backend_process") as start:
                self.assertFalse(server_launcher.ensure_backend_running(timeout=0))
                terminate.assert_not_called()
                start.assert_not_called()

    def test_access_denied_port_scan_is_reported_as_unknown(self):
        with patch.object(
            server_launcher.psutil,
            "net_connections",
            side_effect=server_launcher.psutil.AccessDenied(),
        ):
            listener = server_launcher._identify_listener(server_launcher.PORT)
        self.assertTrue(listener.unknown)
        self.assertFalse(listener.is_insighter)

    def test_listener_must_run_this_projects_app_py(self):
        connection = type("Connection", (), {})()
        connection.status = server_launcher.psutil.CONN_LISTEN
        connection.laddr = type("Address", (), {"port": server_launcher.PORT})()
        connection.pid = 321
        with patch.object(server_launcher.psutil, "Process") as process:
            process.return_value.name.return_value = "python.exe"
            process.return_value.cmdline.return_value = ["python.exe", "other_app.py"]
            process.return_value.cwd.return_value = server_launcher.PROJECT_ROOT
            with patch.object(
                server_launcher.psutil,
                "net_connections",
                return_value=[connection],
            ), patch.object(server_launcher.requests, "get") as get:
                listener = server_launcher._identify_listener(server_launcher.PORT)
            self.assertFalse(listener.is_insighter)
            get.assert_not_called()

    def test_legacy_relative_app_py_with_project_cwd_is_identified(self):
        connection = type("Connection", (), {})()
        connection.status = server_launcher.psutil.CONN_LISTEN
        connection.laddr = type("Address", (), {"port": server_launcher.PORT})()
        connection.pid = 321
        with patch.object(server_launcher.psutil, "Process") as process:
            process.return_value.name.return_value = "python.exe"
            process.return_value.cmdline.return_value = ["python.exe", "app.py"]
            process.return_value.cwd.return_value = server_launcher.PROJECT_ROOT
            with patch.object(
                server_launcher.psutil,
                "net_connections",
                return_value=[connection],
            ), patch.object(
                server_launcher.requests,
                "get",
                side_effect=server_launcher.requests.ConnectionError,
            ):
                listener = server_launcher._identify_listener(server_launcher.PORT)
        self.assertTrue(listener.is_insighter)
        self.assertEqual(listener.pid, 321)

    def test_termination_rechecks_listener_identity(self):
        identified = server_launcher.Listener(321, True, None, "python.exe", False)
        changed = server_launcher.Listener(321, False, None, "python.exe", False)
        with patch.object(server_launcher, "_identify_listener", return_value=changed), \
                patch.object(server_launcher.psutil, "Process") as process:
            self.assertFalse(server_launcher._terminate_listener(identified))
        process.assert_not_called()


if __name__ == "__main__":
    unittest.main()
