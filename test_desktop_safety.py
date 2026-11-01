import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt, QSettings
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication, QBoxLayout, QLabel

from desktop_app.api_client import ApiError
from desktop_app.alerts_tab import AlertsTab
from desktop_app import connection_state
from desktop_app.connection_state import CONNECTED, OFFLINE, ConnectionMonitor
from desktop_app.dashboard_tab import DashboardTab, risk_level_for, score_color
from desktop_app.main import InSighterApp
from desktop_app.profiles_tab import ProfilesTab
from desktop_app.risk import risk_color_for_level
from desktop_app.safe_text import plain_label
from desktop_app import server_launcher
from desktop_app.theme import BG, GridBackground, InsetShadowFrame, QSS


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

    def test_app_stays_running_while_replacing_login_window(self):
        with patch("desktop_app.main.QApplication") as application_class:
            app = InSighterApp()

        self.assertIs(app.qapp, application_class.return_value)
        application_class.return_value.setQuitOnLastWindowClosed.assert_called_once_with(
            False
        )

    def test_first_dashboard_refresh_fetches_thresholds(self):
        client = Mock()
        client.username = "test-user"
        client.get_scores.return_value = []
        client.get_alerts.return_value = []
        client.get_resources.return_value = {}
        client.get_uav_config.return_value = {
            "threshold_high": 50,
            "threshold_medium": 40,
        }
        tab = DashboardTab(client)

        with patch("desktop_app.dashboard_tab.run_async") as run_async:
            tab.refresh()

        fetch = run_async.call_args.args[0]
        self.assertIn("thresholds", fetch())
        client.get_uav_config.assert_called_once_with()

    def test_grid_background_uses_ink_black_with_white_grid_lines(self):
        background = GridBackground()
        background.setStyleSheet(QSS)
        background.resize(56, 56)
        image = QImage(background.size(), QImage.Format.Format_ARGB32)
        background.render(image)

        self.assertEqual(image.pixelColor(2, 2), QColor(BG))
        self.assertGreater(image.pixelColor(0, 10).red(), QColor(BG).red())

        plain_background = GridBackground(draw_grid=False)
        plain_background.setStyleSheet(QSS)
        plain_background.resize(56, 56)
        plain_image = QImage(plain_background.size(), QImage.Format.Format_ARGB32)
        plain_background.render(plain_image)
        self.assertEqual(plain_image.pixelColor(0, 10), QColor(BG))

    def test_dashboard_card_has_subtle_inset_shadow(self):
        card = InsetShadowFrame()
        card.setObjectName("card")
        card.setStyleSheet(QSS)
        card.resize(80, 80)
        image = QImage(card.size(), QImage.Format.Format_ARGB32)
        card.render(image)

        self.assertLess(
            image.pixelColor(40, 3).lightness(),
            image.pixelColor(40, 40).lightness(),
        )

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

    def test_dashboard_risk_colors_follow_server_levels(self):
        self.assertEqual(risk_level_for(50, 50, 40), "HIGH")
        self.assertEqual(risk_level_for(40, 50, 40), "MEDIUM")
        self.assertEqual(risk_level_for(39.9, 50, 40), "LOW")
        self.assertEqual(risk_color_for_level("HIGH"), "#ef4444")
        self.assertEqual(risk_color_for_level("MEDIUM"), "#f59e0b")
        self.assertEqual(risk_color_for_level("LOW"), "#10b981")

    def test_dashboard_counts_and_badges_keep_server_risk_level(self):
        tab = DashboardTab(object())
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
        self.assertIn("#ef4444", score_widget.findChildren(QLabel)[0].styleSheet())
        self.assertEqual(tab.card_top.value_label.text(), "60%")
        self.assertEqual(tab.history["operator"], [(60.0, "HIGH")])

    def test_total_risk_shows_top_user_score_and_empty_values_are_zero(self):
        tab = DashboardTab(object())
        self.assertEqual(tab.card_high.value_label.text(), "Loading…")
        tab._refresh_scores([])
        self.assertEqual(tab.card_top.value_label.text(), "0%")
        self.assertEqual(tab.card_high.value_label.text(), "0")
        self.assertEqual(tab.card_medium.value_label.text(), "0")
        self.assertEqual(tab.card_low.value_label.text(), "0")
        tab._refresh_scores([{
            "user": "high",
            "role": "analyst",
            "risk_score": 72,
            "risk_level": "HIGH",
            "if_score": 60,
            "ocsvm_score": 65,
            "avg_files": 2,
            "avg_transfer": 3,
            "off_hours": 0,
        }, {
            "user": "low",
            "role": "analyst",
            "risk_score": 20,
            "risk_level": "LOW",
            "if_score": 10,
            "ocsvm_score": 15,
            "avg_files": 1,
            "avg_transfer": 1,
            "off_hours": 0,
        }])
        self.assertEqual(tab.card_top.value_label.text(), "72%")
        self.assertEqual(
            tab.metric_table.horizontalScrollBarPolicy(),
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
        )

    def test_summary_values_are_36px_and_matrix_filter_defaults_to_high(self):
        tab = DashboardTab(object())
        for card in (tab.card_top, tab.card_high, tab.card_medium, tab.card_low):
            self.assertIn("font-size:36px", card.value_label.styleSheet())

        users = []
        for username, level, score in (
            ("high_user", "HIGH", 70),
            ("medium_user", "MEDIUM", 35),
            ("low_user", "LOW", 10),
        ):
            users.append({
                "user": username,
                "role": "analyst",
                "risk_score": score,
                "risk_level": level,
                "if_score": score,
                "ocsvm_score": score,
                "avg_files": 1,
                "avg_transfer": 1,
                "off_hours": 0,
            })

        tab._refresh_scores(users)
        self.assertEqual(tab.matrix_risk_filter.currentData(), "HIGH")
        self.assertEqual(tab.table.rowCount(), 1)
        self.assertEqual(
            tab.table.cellWidget(0, 0).findChildren(QLabel)[1].text(),
            "high_user",
        )
        self.assertEqual(
            [
                tab.table.horizontalHeaderItem(column).text()
                for column in range(tab.table.columnCount())
            ],
            [
                "User", "IF score", "SVM score", "Risk score", "Level",
                "Files / event", "Data / event", "Off-hours", "Trend",
            ],
        )
        self.assertEqual(tab.table.item(0, 1).text(), "70.0")
        self.assertEqual(tab.table.item(0, 5).text(), "1.0")
        self.assertEqual(tab.table.item(0, 6).text(), "1.0 MB")
        self.assertEqual(tab.table.item(0, 7).text(), "0")
        self.assertEqual(tab.table.rowHeight(0), 42)
        self.assertEqual(
            tab.table.horizontalScrollBarPolicy(),
            Qt.ScrollBarPolicy.ScrollBarAsNeeded,
        )
        self.assertIn(
            "Isolation Forest anomaly score",
            tab.table.horizontalHeaderItem(1).toolTip(),
        )

        for level, expected_user in (
            ("MEDIUM", "medium_user"),
            ("LOW", "low_user"),
        ):
            tab.matrix_risk_filter.setCurrentIndex(
                tab.matrix_risk_filter.findData(level)
            )
            self.assertEqual(tab.table.rowCount(), 1)
            self.assertEqual(
                tab.table.cellWidget(0, 0).findChildren(QLabel)[1].text(),
                expected_user,
            )

        tab.matrix_risk_filter.setCurrentIndex(
            tab.matrix_risk_filter.findData(None)
        )
        self.assertEqual(tab.table.rowCount(), 3)

    def test_user_profiles_show_current_risk_and_recent_activity(self):
        tab = ProfilesTab(Mock())
        users = [{
            "user": "researcher_01",
            "role": "researcher",
            "risk_score": 47.2,
            "risk_level": "HIGH",
        }]
        event = {
            "timestamp": "2026-10-28 12:34:56",
            "accessed": "projects",
            "login_hour": 21,
            "files_accessed": 12,
            "data_transferred_mb": 42.5,
            "failed_logins": 1,
            "off_hours_access": 1,
            "source": "api",
        }

        with patch("desktop_app.profiles_tab.run_async") as run_async:
            tab._apply_refresh(users)
            on_success = run_async.call_args.args[1]
            on_success([event])

        self.assertIn("Current risk: 47.2% · HIGH", tab.risk_label.text())
        self.assertEqual(tab.history_table.rowCount(), 1)
        self.assertEqual(tab.history_table.item(0, 0).text(), event["timestamp"])
        self.assertEqual(tab.history_table.item(0, 1).text(), "projects")
        self.assertEqual(tab.history_table.item(0, 2).text(), "21")
        self.assertEqual(tab.history_table.item(0, 3).text(), "12")
        self.assertEqual(tab.history_table.item(0, 4).text(), "42.5")
        self.assertEqual(tab.history_table.item(0, 5).text(), "1")
        self.assertEqual(tab.history_table.item(0, 6).text(), "1")
        self.assertEqual(tab.history_table.item(0, 7).text(), "api")

    def test_matrix_filter_spacing_adapts_to_small_and_large_panels(self):
        tab = DashboardTab(object())

        tab._resize_matrix_controls(300, 240)
        self.assertEqual(
            tab.matrix_filter_layout.direction(),
            QBoxLayout.Direction.TopToBottom,
        )
        self.assertLessEqual(tab.matrix_risk_filter.maximumWidth(), 260)

        tab._resize_matrix_controls(700, 420)
        self.assertEqual(
            tab.matrix_filter_layout.direction(),
            QBoxLayout.Direction.LeftToRight,
        )
        self.assertEqual(tab.matrix_risk_filter.maximumWidth(), 150)

    def test_dashboard_alert_feed_filters_by_severity(self):
        tab = DashboardTab(object())
        alerts = [
            {
                "id": f"alert-{severity.lower()}",
                "user": f"{severity.lower()}_user",
                "login_hour": 10,
                "description": f"{severity} alert",
                "flagged_by": "test",
                "final_score": 80,
                "severity": severity,
            }
            for severity in ("CRITICAL", "HIGH", "MEDIUM")
        ]

        tab._refresh_alerts(alerts)
        self.assertEqual(tab.alert_severity_filter.currentData(), "CRITICAL")
        self.assertEqual(tab.alert_meta.property("alert_count"), 1)
        self.assertEqual(tab.alert_meta.text(), "1 of 3 alerts")
        self.assertEqual(tab.alert_layout.count() - 1, 1)

        tab.alert_severity_filter.setCurrentIndex(
            tab.alert_severity_filter.findData("HIGH")
        )
        self.assertEqual(tab.alert_layout.count() - 1, 1)
        rendered_alert = tab.alert_layout.itemAt(0).widget()
        self.assertIn(
            "high_user",
            [label.text() for label in rendered_alert.findChildren(QLabel)],
        )

        tab.alert_severity_filter.setCurrentIndex(
            tab.alert_severity_filter.findData(None)
        )
        self.assertEqual(tab.alert_meta.property("alert_count"), 3)
        self.assertEqual(tab.alert_meta.text(), "3 alerts")
        self.assertEqual(tab.alert_layout.count() - 1, 3)

    def test_snap_grid_is_mouse_transparent(self):
        tab = DashboardTab(object())
        self.assertTrue(
            tab.workspace.grid_layer.testAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents
            )
        )

    def test_dashboard_layout_is_snap_aligned_non_overlapping_and_persisted_per_user(self):
        class MemorySettings:
            def __init__(self):
                self.values = {}

            def value(self, key, default=""):
                return self.values.get(key, default)

            def setValue(self, key, value):
                self.values[key] = value

            def sync(self):
                pass

            @staticmethod
            def status():
                return QSettings.Status.NoError

        settings = MemorySettings()
        client = type("Client", (), {"username": "layout-user"})()
        tab = DashboardTab(client, settings)
        workspace = tab.workspace

        self.assertEqual(
            tab.metric_table.horizontalHeaderItem(0).text(),
            "Total Risk %",
        )
        self.assertTrue(workspace.move_panel("alerts", 8, 14))
        self.assertFalse(workspace.move_panel("alerts", 7, 2))
        self.assertFalse(workspace.resize_or_move("summary", 0, 0, 5, 3))
        workspace.hide_panel("matrix")

        restored = DashboardTab(client, settings)
        self.assertEqual(restored.workspace.positions["alerts"][:2], [8, 14])
        self.assertNotIn("matrix", restored.workspace.visible)
        self.assertNotEqual(
            restored.layout_settings_key,
            DashboardTab(type("Client", (), {"username": "another-user"})(), settings)
            .layout_settings_key,
        )
        self.assertEqual(
            restored.workspace.panels["alerts"].geometry().x(),
            round(8 * restored.workspace.width() / 12
                  + max(1, round(restored.workspace.width() / 12 * 0.06))),
        )
        restored.workspace.hide_panel("matrix")
        restored.workspace.hide_panel("alerts")
        restored.workspace.hide_panel("summary")
        self.assertTrue(restored.workspace.move_panel("matrix", 0, 2))
        restored.workspace.show_panel("matrix")
        self.assertEqual(restored.workspace.positions["matrix"][1], 2)
        self.assertNotIn("history", restored.workspace.panels)
        self.assertNotIn("history", restored.workspace.positions)

    def test_default_risk_summary_matches_compact_dashboard_layout(self):
        tab = DashboardTab(object())
        workspace = tab.workspace
        workspace.resize(1272, 720)
        workspace.layout_panels()
        cell, row, gap = workspace.grid_metrics()

        self.assertAlmostEqual(row / cell, 0.28)
        self.assertEqual(tab.panels["summary"].height(), round(8 * row - 2 * gap))
        self.assertEqual(
            tab.panels["matrix"].geometry().y(),
            round(8 * row + gap),
        )
        self.assertEqual(tab.workspace.positions["matrix"], [0, 8, 8, 16])
        self.assertEqual(tab.workspace.positions["alerts"], [8, 8, 4, 16])
        self.assertNotIn("history", tab.workspace.positions)

    def test_removed_risk_history_is_dropped_from_saved_layout(self):
        class MemorySettings:
            def __init__(self):
                self.values = {}

            def value(self, key, default=""):
                return self.values.get(key, default)

            def setValue(self, key, value):
                self.values[key] = value

            def sync(self):
                pass

            @staticmethod
            def status():
                return QSettings.Status.NoError

        settings = MemorySettings()
        client = type("Client", (), {"username": "layout-user"})()
        tab = DashboardTab(client, settings)
        settings.values[tab.layout_settings_key] = (
            '{"positions":{"summary":[0,0,12,8],'
            '"matrix":[0,8,8,16],"alerts":[8,8,4,16],'
            '"history":[0,24,12,7]},'
            '"visible":["summary","matrix","alerts","history"]}'
        )

        restored = DashboardTab(client, settings)
        saved = settings.value(restored.layout_settings_key)

        self.assertNotIn("history", restored.workspace.panels)
        self.assertNotIn("history", restored.workspace.visible)
        self.assertNotIn("history", restored.workspace.positions)
        self.assertNotIn('"history"', saved)

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
