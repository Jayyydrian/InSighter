import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from desktop_app.api_client import ApiClient
from desktop_app.dashboard_tab import DashboardTab


class OverviewAlertTotalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def test_api_client_preserves_rows_and_reads_total_header(self):
        alerts = [{"id": alert_id} for alert_id in range(25)]
        response = Mock(
            status_code=200,
            headers={"X-Total-Flagged": "31"},
        )
        response.json.return_value = alerts
        client = ApiClient()

        with patch.object(client, "_request", return_value=response):
            page = client.get_alerts_page()

        self.assertEqual(page, {"alerts": alerts, "total": 31})

    def _get_alerts_page(self, alerts, headers):
        response = Mock(status_code=200, headers=headers)
        response.json.return_value = alerts
        client = ApiClient()
        with patch.object(client, "_request", return_value=response):
            return client.get_alerts_page()

    def test_api_client_falls_back_when_total_header_is_missing(self):
        alerts = [{"id": alert_id} for alert_id in range(3)]
        self.assertEqual(
            self._get_alerts_page(alerts, {}),
            {"alerts": alerts, "total": 3},
        )

    def test_api_client_falls_back_when_total_header_is_not_an_integer(self):
        alerts = [{"id": alert_id} for alert_id in range(3)]
        self.assertEqual(
            self._get_alerts_page(alerts, {"X-Total-Flagged": "abc"}),
            {"alerts": alerts, "total": 3},
        )

    def test_api_client_clamps_negative_total_to_returned_rows(self):
        alerts = [{"id": alert_id} for alert_id in range(3)]
        self.assertEqual(
            self._get_alerts_page(alerts, {"X-Total-Flagged": "-5"}),
            {"alerts": alerts, "total": 3},
        )

    def test_api_client_clamps_total_smaller_than_returned_rows(self):
        alerts = [{"id": alert_id} for alert_id in range(5)]
        self.assertEqual(
            self._get_alerts_page(alerts, {"X-Total-Flagged": "3"}),
            {"alerts": alerts, "total": 5},
        )

    def test_api_client_preserves_total_larger_than_returned_rows(self):
        alerts = [{"id": alert_id} for alert_id in range(5)]
        self.assertEqual(
            self._get_alerts_page(alerts, {"X-Total-Flagged": "8"}),
            {"alerts": alerts, "total": 8},
        )

    def test_dashboard_shows_total_and_truncated_page_count(self):
        tab = DashboardTab(object())
        self.addCleanup(tab.close)
        alerts = [
            {
                "id": alert_id,
                "severity": "HIGH",
                "user": f"test-user-{alert_id}",
                "login_hour": 10,
                "description": "Test alert",
                "flagged_by": "IF",
                "final_score": 80,
            }
            for alert_id in range(25)
        ]

        tab._refresh_alerts({"alerts": alerts, "total": 31})

        self.assertEqual(tab.alert_meta.property("alert_count"), 31)
        self.assertEqual(tab.alert_meta.text(), "31 alerts · showing 25 of 31")


if __name__ == "__main__":
    unittest.main()
