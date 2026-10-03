import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from desktop_app.uav_config_tab import UavConfigTab


class FakeClient:
    def __init__(self):
        self.config = {
            "sector": "sme_startup",
            "taxonomy": {"roles": ["finance", "hr", "it_admin"], "data_categories": ["client_data"]},
            "threshold_high": 45,
            "threshold_medium": 30,
            "sync_interval_minutes": 15,
            "drone_operator_username": "drone_operator",
            "log_targets": "active_directory",
            "updated_at": "test",
        }

    def get_uav_config(self):
        return self.config.copy()

    def get_monitoring_scope(self):
        return {"in_scope_percent": 0, "in_scope_events": 0, "total_events": 0}

    def save_deployment_config(self, payload):
        self.config.update(payload)
        self.config["taxonomy"] = {
            "roles": ["drone_operator"],
            "data_categories": ["drone_platform_access", "disaster_data"],
        }


class UavConfigSectorRefreshTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_periodic_refresh_preserves_unsaved_sector_selection(self):
        tab = UavConfigTab(FakeClient())
        tab.refresh()
        tab.sector.setCurrentIndex(tab.sector.findData("uav_disaster_response"))

        tab.refresh()

        self.assertEqual(tab.sector.currentData(), "uav_disaster_response")

    def test_save_persists_sector_and_subsequent_refresh_uses_it(self):
        client = FakeClient()
        tab = UavConfigTab(client)
        tab.refresh()
        tab.sector.setCurrentIndex(tab.sector.findData("uav_disaster_response"))

        tab._save()

        self.assertEqual(client.config["sector"], "uav_disaster_response")
        self.assertEqual(tab.sector.currentData(), "uav_disaster_response")


if __name__ == "__main__":
    unittest.main()