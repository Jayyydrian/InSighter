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
import model
from auth import init_users_table
from desktop_app.alerts_tab import AlertsTab
from desktop_app.audit_tab import AuditTab
from desktop_app.dashboard_tab import DashboardTab
from desktop_app.integrations_tab import IntegrationsTab
from desktop_app.main_window import MainWindow
from desktop_app.profiles_tab import ProfilesTab
from desktop_app.resources_tab import ResourcesTab
from desktop_app.role_baseline_setup_tab import RoleBaselineSetupTab
from desktop_app.role_sessions_tab import RoleSessionsTab
from desktop_app.sessions_tab import SessionsTab
from desktop_app.uav_config_tab import UavConfigTab


def score_row(user="phase3-user", risk_score=72.5, risk_level="HIGH"):
    return {
        "user": user,
        "role": "researcher",
        "risk_score": risk_score,
        "risk_level": risk_level,
        "if_score": risk_score,
        "ocsvm_score": risk_score,
        "avg_files": 10.0,
        "avg_transfer": 2.0,
        "avg_failed_logins": 0.0,
        "off_hours": 0,
        "total_logs": 10,
        "anomalous_logs": 2,
        "role_baseline_score": 0.0,
    }


class Phase3ContractTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.previous_database_path = database.DB_PATH
        self.database_path = temporary_database_path()
        database.DB_PATH = self.database_path
        database.ensure_database()
        init_users_table()
        create_test_user(
            "phase3-admin",
            "phase3-admin-long-test-password",
            "admin",
        )
        self.client = app_module.app.test_client()
        csrf = self.client.get("/api/csrf-token").get_json()["csrf_token"]
        response = self.client.post(
            "/login",
            data={
                "username": "phase3-admin",
                "password": "phase3-admin-long-test-password",
                "_csrf_token": csrf,
            },
        )
        self.assertEqual(response.status_code, 302)

    def tearDown(self):
        database.DB_PATH = self.previous_database_path
        if os.path.exists(self.database_path):
            os.remove(self.database_path)


class ScoreHistoryContractTests(Phase3ContractTestCase):
    def test_scores_endpoint_returns_risk_score_and_level_records(self):
        expected = [score_row()]
        with patch("app.score_users", return_value=expected):
            response = self.client.get("/api/scores")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload, expected)
        self.assertIsInstance(payload, list)
        self.assertIsInstance(payload[0]["risk_score"], (int, float))
        self.assertIn(payload[0]["risk_level"], {"LOW", "MEDIUM", "HIGH"})

    def test_dashboard_history_consumes_score_and_level_pair(self):
        tab = DashboardTab(object())
        tab._refresh_scores([score_row()])

        self.assertEqual(tab.history["phase3-user"], [(72.5, "HIGH")])
        _bars_layout, value_label = tab._sparkline_rows["phase3-user"]
        self.assertEqual(value_label.text(), "72.5%")
        tab.close()


class RecentAlertsContractTests(Phase3ContractTestCase):
    @staticmethod
    def _scored_alert_rows():
        import pandas as pd

        rows = [
            {
                "id": alert_id,
                "user": f"phase3-user-{alert_id}",
                "role": "researcher",
                "login_hour": 12,
                "off_hours_access": 0,
                "data_category": "projects",
                "files_accessed": 1,
                "data_transferred_mb": 1.0,
                "failed_logins": 0,
                "if_score": float(alert_id),
                "ocsvm_score": float(alert_id),
                "final_score": float(alert_id),
                "is_anomaly": 1,
            }
            for alert_id in range(1, 31)
        ]
        return pd.DataFrame(rows)

    def test_get_recent_alerts_uses_documented_default_pagination(self):
        scored_rows = self._scored_alert_rows()
        raw_rows = scored_rows.drop(
            columns=["if_score", "ocsvm_score", "final_score", "is_anomaly"]
        )
        with (
            patch("model.get_active_sector", return_value="private_school"),
            patch("model.get_sector_config", return_value={"roles": ["researcher"]}),
            patch("model.connect", return_value=Mock()),
            patch("model.pd.read_sql", return_value=raw_rows),
            patch("model._run_ensemble", return_value=scored_rows),
        ):
            defaults = model.get_recent_alerts()
            explicit_defaults = model.get_recent_alerts(
                limit=25,
                offset=0,
                sort="priority",
            )

        self.assertEqual(defaults, explicit_defaults)
        self.assertEqual(len(defaults), 25)
        self.assertEqual(
            [alert["id"] for alert in defaults],
            list(range(30, 5, -1)),
        )

    def test_alert_api_applies_explicit_limit_offset_and_sort(self):
        alerts = [
            {"id": alert_id, "user": f"user-{alert_id}"}
            for alert_id in (5, 4, 3, 2, 1)
        ]
        with patch("app.get_recent_alerts", return_value=alerts) as get_alerts:
            response = self.client.get(
                "/api/alerts?limit=2&offset=1&sort=newest"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual([alert["id"] for alert in response.get_json()], [4, 3])
        self.assertEqual(response.headers["X-Total-Flagged"], "5")
        self.assertEqual(response.headers["X-Returned"], "2")
        get_alerts.assert_called_once_with(limit=None, sort="newest")


class RefreshTickContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def test_page_visits_refresh_separately_from_timer_timeouts(self):
        refresh_events = []
        timer_events = []

        class FakeSignal:
            def __init__(self):
                self.callbacks = []

            def connect(self, callback):
                self.callbacks.append(callback)

            def emit(self):
                for callback in self.callbacks:
                    callback()

        class FakeTimer:
            def __init__(self, _parent):
                self.timeout = FakeSignal()
                self.interval = None
                self.active = False

            def start(self, interval=None):
                if interval is not None:
                    self.interval = interval
                self.active = True

            def setInterval(self, interval):
                self.interval = interval

            def stop(self):
                self.active = False

        class FakeClient:
            username = "phase3-admin"
            role = "admin"
            connection_monitor = None
            config = {
                "sector": "private_school",
                "taxonomy": {
                    "roles": ["researcher", "intern", "admin_staff", "it_staff"],
                    "data_categories": ["projects", "private_documents", "pii"],
                },
                "threshold_high": 45,
                "threshold_medium": 30,
                "sync_interval_minutes": 15,
                "drone_operator_username": "drone_operator",
                "log_targets": "active_directory",
                "updated_at": "test",
            }

        refresh_tabs = (
            DashboardTab,
            SessionsTab,
            ProfilesTab,
            RoleSessionsTab,
            AlertsTab,
            ResourcesTab,
            AuditTab,
            IntegrationsTab,
            RoleBaselineSetupTab,
            UavConfigTab,
        )
        patches = [
            patch.object(
                tab_type,
                "refresh",
                lambda _self, name=tab_type.__name__: refresh_events.append(name),
            )
            for tab_type in refresh_tabs
        ]
        for patcher in patches:
            patcher.start()
        baseline_banner_patcher = patch.object(
            MainWindow, "_update_baseline_banner"
        )
        baseline_banner_patcher.start()
        window = None
        try:
            with patch("desktop_app.main_window.QTimer", FakeTimer):
                window = MainWindow(FakeClient(), on_logout=lambda: None)
            window.timer.stop()
            window.clock_timer.stop()
            self.assertEqual(window.timer.interval, 5000)
            self.assertEqual(refresh_events, ["DashboardTab"])
            refresh_events.clear()
            window.timer.timeout.connect(lambda: timer_events.append(True))
            for _ in range(3):
                window.timer.timeout.emit()
            self.assertEqual(len(timer_events), 3)
            self.assertEqual(len(refresh_events), len(timer_events))
            timer_count = len(timer_events)

            nav_items = list(window._nav_pages)
            window._go_to(nav_items[1])
            window._go_to(nav_items[2])

            self.assertEqual(len(timer_events), timer_count)
            self.assertEqual(
                refresh_events[timer_count:],
                ["SessionsTab", "ProfilesTab"],
            )
        finally:
            if window is not None:
                window.timer.stop()
                window.clock_timer.stop()
                window.close()
            for patcher in reversed(patches):
                patcher.stop()
            baseline_banner_patcher.stop()


if __name__ == "__main__":
    unittest.main()
