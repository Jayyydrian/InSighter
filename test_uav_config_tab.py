import os
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from desktop_app.api_client import ApiError
from desktop_app.uav_config_tab import UavConfigTab


def wait_for(app, predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    app.processEvents()
    return predicate()


class FakeClient:
    def __init__(self):
        self.config = {
            "sector": "private_school",
            "taxonomy": {"roles": ["researcher", "intern", "admin_staff", "it_staff"], "data_categories": ["projects", "private_documents", "intellectual_property", "pii"]},
            "threshold_high": 45,
            "threshold_medium": 30,
            "sync_interval_minutes": 15,
            "drone_operator_username": "drone_operator",
            "log_targets": "active_directory",
            "updated_at": "test",
        }
        self.saved_payload = None
        self.save_error = None
        self.save_delay = 0

    def get_uav_config(self):
        return self.config.copy()

    def get_monitoring_scope(self):
        return {"in_scope_percent": 0, "in_scope_events": 0, "total_events": 0}

    def save_deployment_config(self, payload):
        if self.save_delay:
            time.sleep(self.save_delay)
        self.saved_payload = payload.copy()
        if self.save_error:
            raise self.save_error
        self.config.update(payload)
        self.config["taxonomy"] = {
            "roles": ["drone_operator"],
            "data_categories": ["drone_platform_access", "disaster_data"],
        }
        return self.config.copy()


class UavConfigTabTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_tab(self):
        client = FakeClient()
        tab = UavConfigTab(client)
        tab.refresh()
        self.assertTrue(wait_for(self.app, lambda: bool(tab.updated_label.text())))
        return client, tab

    def test_programmatic_refresh_populates_without_marking_dirty(self):
        client, tab = self.make_tab()
        self.assertEqual(tab.threshold_high.value(), 45)
        self.assertFalse(tab._dirty)
        self.assertTrue(tab.unsaved_label.isHidden())

    def test_refresh_preserves_unsaved_threshold_edit(self):
        client, tab = self.make_tab()
        tab.threshold_high.setValue(72)
        self.assertTrue(tab._dirty)
        client.config["threshold_high"] = 55

        tab.refresh()

        self.assertTrue(wait_for(self.app, lambda: tab.updated_label.text() == "Last updated: test"))
        self.assertEqual(tab.threshold_high.value(), 72)
        self.assertFalse(tab.unsaved_label.isHidden())

    def test_successful_save_clears_dirty_and_applies_server_values(self):
        client, tab = self.make_tab()
        tab.threshold_high.setValue(72)

        tab._save()

        self.assertFalse(tab.save_btn.isEnabled())
        self.assertTrue(wait_for(self.app, lambda: tab.save_btn.isEnabled()))
        self.assertEqual(client.config["threshold_high"], 72)
        self.assertIn("threshold_high", client.saved_payload)
        self.assertNotIn("password", client.saved_payload)
        self.assertFalse(tab._dirty)
        self.assertTrue(tab.unsaved_label.isHidden())
        self.assertEqual(tab.status_label.text(), "Configuration saved.")

    def test_failed_save_keeps_user_values_and_dirty_state(self):
        client, tab = self.make_tab()
        tab.threshold_high.setValue(72)
        client.save_error = ApiError(403, "Server rejected configuration.")

        tab._save()

        self.assertTrue(wait_for(self.app, lambda: tab.save_btn.isEnabled()))
        self.assertEqual(tab.threshold_high.value(), 72)
        self.assertTrue(tab._dirty)
        self.assertIn("Server rejected configuration.", tab.status_label.text())

    def test_edits_made_during_save_remain_unsaved(self):
        client, tab = self.make_tab()
        client.save_delay = 0.15
        tab.threshold_high.setValue(72)

        tab._save()
        tab.threshold_high.setValue(81)

        self.assertTrue(wait_for(self.app, lambda: tab.save_btn.isEnabled()))
        self.assertEqual(tab.threshold_high.value(), 81)
        self.assertTrue(tab._dirty)
        self.assertIn("Newer edits remain unsaved.", tab.status_label.text())

    def test_reset_discards_edits_and_password_input_is_absent(self):
        client, tab = self.make_tab()
        tab.threshold_high.setValue(72)
        self.assertFalse(hasattr(tab, "drone_password"))

        tab._reset()

        self.assertTrue(wait_for(self.app, lambda: tab.threshold_high.value() == 45))
        self.assertFalse(tab._dirty)
        self.assertEqual(tab.drone_username_help.text(),
                         "Reference name only. Creating a login for this operator is not yet supported.")


if __name__ == "__main__":
    unittest.main()
