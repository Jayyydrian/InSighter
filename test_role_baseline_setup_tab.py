import copy
import os
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLabel, QMainWindow, QStackedWidget, QWidget, QMessageBox

from desktop_app.api_client import ApiClient, ApiError
from desktop_app.main_window import MainWindow, _NavItem
from desktop_app.role_baseline_setup_tab import (
    HELP_TEXT,
    RoleBaselineSetupTab,
)
from desktop_app.safe_text import plain_label


def wait_for(app, predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    app.processEvents()
    return predicate()


def baseline_payload(status="not_configured", role_name="researcher", label_override=None):
    fields = [
        ("files_accessed", "Average files accessed per event", "files/event", 5, 10, 155),
        ("data_transferred_mb", "Average data transferred per event", "MB/event", 20, 20, 1202),
        ("failed_logins", "Average failed logins per event", "attempts/event", 0.5, 0.3, 13.5),
        ("off_hours_access", "Off-hours event fraction", "fraction of events", 0.2, 0.1, 1),
    ]
    roles = []
    items = []
    for role in (role_name, "it_staff"):
        features = []
        for feature, label, unit, default, observed, maximum in fields:
            item = {
                "role": role,
                "feature": feature,
                "label": label_override or label,
                "unit": unit,
                "min": 0,
                "max": maximum,
                "default_value": default,
                "declared_value": None if status != "configured" else default,
                "effective_value": default,
                "observed": {"mean": observed},
                "warning": None,
            }
            features.append(item)
            items.append(item)
        roles.append({"role": role, "features": features})
    return {
        "sector": "private_school",
        "status": status,
        "completed_by": None,
        "completed_at": None,
        "items": items,
        "roles": roles,
    }


def private_school_payload(status="not_configured"):
    payload = baseline_payload(status=status, role_name="researcher")
    role_names = ("researcher", "intern", "admin_staff", "it_staff")
    defaults = {
        "files_accessed": 8.267605633802816,
        "data_transferred_mb": 21.202852112676055,
        "failed_logins": 0.4595070422535211,
        "off_hours_access": 0.04,
    }
    roles = []
    items = []
    for role in role_names:
        role_data = copy.deepcopy(payload["roles"][0])
        role_data["role"] = role
        for item in role_data["features"]:
            item["role"] = role
            item["default_value"] = defaults[item["feature"]]
            item["effective_value"] = defaults[item["feature"]]
            item["declared_value"] = (
                defaults[item["feature"]] if status == "configured" else None
            )
            item["observed"]["mean"] = defaults[item["feature"]]
        roles.append(role_data)
        items.extend(role_data["features"])
    payload.update({"sector": "private_school", "roles": roles, "items": items})
    return payload


class FakeClient:
    def __init__(self, status="not_configured", role_name="researcher", label_override=None):
        self.role = "admin"
        self.username = "test-admin"
        self.connection_monitor = None
        self.payload = baseline_payload(status, role_name, label_override)
        self.save_error = None
        self.save_delay = 0
        self.saved_payload = None
        self.reset_calls = 0
        self.call_threads = []
        self.get_calls = 0
        self.save_calls = []

    def get_baseline_setup(self):
        self.get_calls += 1
        self.call_threads.append(threading.get_ident())
        return copy.deepcopy(self.payload)

    def save_baseline_setup(self, payload):
        self.call_threads.append(threading.get_ident())
        self.save_calls.append(copy.deepcopy(payload))
        if self.save_delay:
            time.sleep(self.save_delay)
        if self.save_error:
            raise self.save_error
        self.saved_payload = copy.deepcopy(payload)
        self.payload["status"] = "configured" if payload.get("complete") else "not_configured"
        by_key = {
            (value["role"], value["feature"]): value["value"]
            for value in payload["values"]
        }
        for item in self.payload["items"]:
            key = (item["role"], item["feature"])
            if key in by_key:
                item["declared_value"] = by_key[key]
                item["effective_value"] = by_key[key]
        return {"status": "saved", "configured": payload.get("complete", False)}

    def reset_baseline_setup(self):
        self.call_threads.append(threading.get_ident())
        self.reset_calls += 1
        self.payload["status"] = "not_configured"
        for item in self.payload["items"]:
            item["declared_value"] = None
            item["effective_value"] = item["default_value"]
        return {"status": "reset"}


class RoleBaselineSetupTabTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_tab(self, **kwargs):
        client = FakeClient(**kwargs)
        tab = RoleBaselineSetupTab(client)
        tab.refresh()
        self.assertTrue(wait_for(self.app, lambda: tab.pages.count() > 0))
        return client, tab

    def make_private_school_tab(self, status="not_configured"):
        client = FakeClient(status=status, role_name="researcher")
        client.payload = private_school_payload(status)
        tab = RoleBaselineSetupTab(client)
        tab.refresh()
        self.assertTrue(wait_for(self.app, lambda: tab.pages.count() > 0))
        return client, tab

    def test_next_visits_all_roles_and_review_without_client_calls(self):
        client, tab = self.make_private_school_tab()
        initial_get_calls = client.get_calls
        role_defaults = {
            key: field["default"] for key, field in tab._controls.items()
        }
        self.assertTrue(tab.unsaved_label.isHidden())
        self.assertTrue(all(not field["edited"] for field in tab._controls.values()))

        for index, role in enumerate(("researcher", "intern", "admin_staff", "it_staff")):
            self.assertEqual(tab._step, index)
            self.assertIn(role, [
                label.text() for label in tab.pages.currentWidget().findChildren(QLabel)
            ])
            tab.next_button.click()
            self.app.processEvents()

        self.assertEqual(tab.step_label.text(), "Review")
        self.assertEqual(tab._current_values(), role_defaults)
        self.assertTrue(all(not field["edited"] for field in tab._controls.values()))
        self.assertEqual(client.get_calls, initial_get_calls)
        self.assertEqual(client.save_calls, [])

    def test_unedited_defaults_survive_navigation_and_refresh_exactly(self):
        _client, tab = self.make_private_school_tab()
        exact_defaults = {
            key: field["default"] for key, field in tab._controls.items()
        }

        tab.next_button.click()
        tab.refresh()
        self.assertTrue(wait_for(self.app, lambda: not tab._request_in_flight))
        tab.back_button.click()
        tab.next_button.click()

        self.assertEqual(tab._current_values(), exact_defaults)
        self.assertTrue(all(not field["edited"] for field in tab._controls.values()))
        self.assertFalse(tab._dirty)
        self.assertTrue(tab.unsaved_label.isHidden())

    def test_display_units_show_whole_numbers_and_use_scaled_ranges(self):
        _client, tab = self.make_private_school_tab()
        expected = {
            "files_accessed": (8, "files per event", 1),
            "data_transferred_mb": (21, "MB per event", 5),
            "failed_logins": (46, "failed logins per 100 events", 5),
            "off_hours_access": (4, "% of events", 1),
        }
        for feature, (display_value, unit, step) in expected.items():
            field = tab._controls[("researcher", feature)]
            self.assertEqual(field["spin"].decimals(), 0)
            self.assertEqual(field["spin"].value(), display_value)
            self.assertEqual(field["spin"].singleStep(), step)
            self.assertEqual(field["display_unit"], unit)
            self.assertIn(unit, field["default_label"].text())
            self.assertIn(str(display_value), field["default_label"].text())
            self.assertIn(unit, field["observed_label"].text())
            self.assertIn(str(display_value), field["observed_label"].text())

        for _ in range(4):
            tab.next_button.click()
        review_text = "\n".join(
            label.text() for label in tab.pages.currentWidget().findChildren(QLabel)
        )
        for feature, (display_value, unit, _step) in expected.items():
            self.assertIn(f"{display_value} {unit}", review_text)

    def test_edits_convert_only_selected_fields_to_api_units(self):
        client, tab = self.make_private_school_tab()
        edited_values = {
            ("researcher", "files_accessed"): 12.0,
            ("intern", "failed_logins"): 0.46,
            ("admin_staff", "off_hours_access"): 0.05,
        }
        exact_defaults = {
            key: field["default"] for key, field in tab._controls.items()
        }
        tab._controls[("researcher", "files_accessed")]["spin"].setValue(12)
        failed_login_spin = tab._controls[("intern", "failed_logins")]["spin"]
        failed_login_spin.lineEdit().textEdited.emit("46")
        failed_login_spin.editingFinished.emit()
        tab._controls[("admin_staff", "off_hours_access")]["spin"].setValue(5)

        for _ in range(4):
            tab.next_button.click()
        with patch(
            "desktop_app.role_baseline_setup_tab.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            tab.finish_button.click()
        self.assertTrue(wait_for(self.app, lambda: len(client.save_calls) == 1))
        self.assertTrue(wait_for(self.app, lambda: not tab._request_in_flight))

        actual = {
            (item["role"], item["feature"]): item["value"]
            for item in client.save_calls[0]["values"]
        }
        for key, default in exact_defaults.items():
            self.assertEqual(actual[key], edited_values.get(key, default))
        self.assertEqual(actual[("intern", "data_transferred_mb")], 21.202852112676055)

    def test_typing_the_displayed_default_is_an_explicit_edit(self):
        _client, tab = self.make_private_school_tab()
        field = tab._controls[("researcher", "files_accessed")]
        self.assertEqual(field["spin"].value(), 8)

        line_edit = field["spin"].lineEdit()
        line_edit.setFocus()
        line_edit.selectAll()
        QTest.keyClicks(line_edit, "8")
        QTest.keyClick(line_edit, Qt.Key.Key_Return)
        self.app.processEvents()

        self.assertEqual(field["exact"], 8.0)
        self.assertTrue(field["edited"])
        self.assertTrue(tab._dirty)

    def test_display_range_rounding_stays_within_api_bounds(self):
        client = FakeClient(status="not_configured", role_name="researcher")
        client.payload = private_school_payload()
        for item in client.payload["items"]:
            if item["feature"] == "failed_logins":
                item["min"], item["max"] = 0.01, 13.5
            elif item["feature"] == "data_transferred_mb":
                item["min"], item["max"] = 0.2, 1202
        tab = RoleBaselineSetupTab(client)
        tab.refresh()
        self.assertTrue(wait_for(self.app, lambda: tab.pages.count() > 0))

        failed = tab._controls[("researcher", "failed_logins")]["spin"]
        transfer = tab._controls[("researcher", "data_transferred_mb")]["spin"]
        self.assertEqual((failed.minimum(), failed.maximum()), (1, 1350))
        self.assertEqual((transfer.minimum(), transfer.maximum()), (1, 1202))
        self.assertGreaterEqual(failed.minimum() / 100, 0.01)
        self.assertLessEqual(failed.maximum() / 100, 13.5)
        self.assertGreaterEqual(transfer.minimum(), 0.2)
        self.assertLessEqual(transfer.maximum(), 1202)

    def test_refresh_keeps_step_and_draft_widgets_in_place(self):
        client, tab = self.make_private_school_tab()
        tab.next_button.click()
        self.assertEqual(tab._step, 1)
        role_page = tab.pages.widget(1)
        field = tab._controls[("intern", "files_accessed")]
        spin = field["spin"]

        tab.refresh()
        self.assertTrue(wait_for(self.app, lambda: not tab._request_in_flight))
        self.assertEqual(tab._step, 1)
        self.assertIs(tab.pages.widget(1), role_page)
        self.assertIs(tab._controls[("intern", "files_accessed")]["spin"], spin)

        spin.setValue(12)
        tab.refresh()
        self.assertTrue(wait_for(self.app, lambda: not tab._request_in_flight))
        self.assertEqual(tab._step, 1)
        self.assertEqual(field["exact"], 12.0)
        self.assertEqual(spin.value(), 12)
        self.assertTrue(field["edited"])
        self.assertIs(tab.pages.widget(1), role_page)

    def test_back_preserves_edits_and_unedited_default_precision(self):
        _client, tab = self.make_private_school_tab()
        exact_default = tab._controls[("researcher", "data_transferred_mb")]["default"]
        field = tab._controls[("researcher", "files_accessed")]
        field["spin"].setValue(12)
        tab.next_button.click()
        tab.back_button.click()

        self.assertEqual(tab._step, 0)
        self.assertEqual(
            {
                (item["role"], item["feature"]): item["value"]
                for item in tab._payload_values()
            }[("researcher", "files_accessed")],
            12.0,
        )
        self.assertEqual(
            {
                (item["role"], item["feature"]): item["value"]
                for item in tab._payload_values()
            }[("researcher", "data_transferred_mb")],
            exact_default,
        )

    def test_active_warning_does_not_block_next(self):
        _client, tab = self.make_private_school_tab()
        field = tab._controls[("researcher", "files_accessed")]
        field["observed"] = 10
        field["spin"].setValue(100)
        self.assertFalse(field["warning"].isHidden())
        self.assertTrue(tab.next_button.isEnabled())

        tab.next_button.click()

        self.assertEqual(tab._step, 1)
        self.assertEqual(
            {
                (item["role"], item["feature"]): item["value"]
                for item in tab._payload_values()
            }[("researcher", "data_transferred_mb")],
            21.202852112676055,
        )

    def test_fresh_wizard_retains_exact_default_values(self):
        _client, tab = self.make_private_school_tab()

        for field in tab._controls.values():
            self.assertEqual(field["exact"], field["default"])
            self.assertFalse(field["edited"])
        self.assertEqual(
            {
                (item["role"], item["feature"]): item["value"]
                for item in tab._payload_values()
            }[("researcher", "files_accessed")],
            8.267605633802816,
        )

    def test_use_defaults_restores_exact_default_to_draft(self):
        _client, tab = self.make_private_school_tab()
        field = tab._controls[("researcher", "files_accessed")]
        field["spin"].setValue(12)

        tab._use_defaults("researcher")

        self.assertEqual(field["exact"], 8.267605633802816)
        self.assertFalse(field["edited"])
        self.assertFalse(tab._dirty)
        self.assertEqual(
            {
                (item["role"], item["feature"]): item["value"]
                for item in tab._payload_values()
            }[("researcher", "files_accessed")],
            8.267605633802816,
        )

    def test_finish_sends_one_complete_request_with_full_precision(self):
        client, tab = self.make_private_school_tab()
        expected_values = {
            key: field["default"] for key, field in tab._controls.items()
        }
        for _ in range(4):
            tab.next_button.click()
            self.app.processEvents()
        self.assertEqual(tab.step_label.text(), "Review")

        with patch(
            "desktop_app.role_baseline_setup_tab.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            tab.finish_button.click()
        self.assertTrue(wait_for(self.app, lambda: len(client.save_calls) == 1))
        self.assertTrue(wait_for(self.app, lambda: not tab._request_in_flight))

        request = client.save_calls[0]
        self.assertTrue(request["complete"])
        self.assertEqual(
            {(item["role"], item["feature"]): item["value"] for item in request["values"]},
            expected_values,
        )

    def test_wizard_defaults_navigation_review_warning_and_finish(self):
        client, tab = self.make_tab()
        self.assertFalse(tab._configured)
        field = tab._controls[("researcher", "data_transferred_mb")]
        self.assertEqual(field["spin"].value(), field["default"])
        self.assertEqual(tab.help_label.text(), HELP_TEXT)
        unit_labels = [label.text() for label in tab.findChildren(QLabel)]
        self.assertIn("MB per event", unit_labels)

        field["spin"].setValue(121)
        self.assertFalse(field["warning"].isHidden())
        self.assertIn("more than 5x", field["warning"].text())

        tab._next()
        self.assertEqual(tab._step, 1)
        tab._next()
        self.assertEqual(tab.step_label.text(), "Review")
        review_text = "\n".join(
            label.text() for label in tab.pages.currentWidget().findChildren(QLabel)
        )
        self.assertIn("chosen: 121 MB per event", review_text)
        self.assertIn("default: 20 MB per event", review_text)
        self.assertIn("more than 5x", review_text)

        with patch(
            "desktop_app.role_baseline_setup_tab.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            tab._finish_setup()
        self.assertTrue(wait_for(self.app, lambda: client.saved_payload is not None))
        self.assertTrue(client.saved_payload["complete"])
        self.assertTrue(tab._configured)
        self.assertTrue(wait_for(self.app, lambda: not tab._request_in_flight))

    def test_use_defaults_restores_current_role_values(self):
        _client, tab = self.make_tab()
        field = tab._controls[("researcher", "files_accessed")]
        field["spin"].setValue(22)
        tab._use_defaults("researcher")
        self.assertEqual(field["spin"].value(), field["default"])

    def test_configured_refresh_preserves_edits_and_customized_marker(self):
        client, tab = self.make_tab(status="configured")
        field = tab._controls[("researcher", "data_transferred_mb")]
        self.assertFalse(tab.save_button.isEnabled())
        field["spin"].setValue(120)
        self.assertTrue(tab._dirty)
        tab.refresh()
        self.assertTrue(wait_for(self.app, lambda: not tab._request_in_flight))
        self.assertEqual(field["spin"].value(), 120)
        self.assertFalse(field["customized"].isHidden())
        self.assertTrue(tab.save_button.isEnabled())
        self.assertTrue(tab.discard_button.isEnabled())
        self.assertNotEqual(client.call_threads[-1], threading.get_ident())

    def test_discard_restores_last_saved_values(self):
        _client, tab = self.make_private_school_tab(status="configured")
        field = tab._controls[("researcher", "files_accessed")]
        saved = field["spin"].value()
        exact_saved = field["exact"]
        field["spin"].setValue(saved + 5)
        self.assertTrue(tab.discard_button.isEnabled())
        tab._discard()
        self.assertEqual(field["spin"].value(), saved)
        self.assertEqual(field["exact"], exact_saved)
        self.assertFalse(field["edited"])
        self.assertFalse(tab._dirty)
        self.assertFalse(tab.save_button.isEnabled())

    def test_failed_save_retains_edits_and_displays_server_message_as_plain_text(self):
        client, tab = self.make_tab(status="configured")
        field = tab._controls[("researcher", "files_accessed")]
        field["spin"].setValue(12)
        client.save_error = ApiError(400, "<img src=x onerror=alert(1)> rejected")
        tab._save()
        self.assertTrue(wait_for(self.app, lambda: not tab._request_in_flight))
        self.assertEqual(field["spin"].value(), 12)
        self.assertTrue(tab._dirty)
        self.assertIn("<img src=x onerror=alert(1)>", tab.status_label.text())
        self.assertEqual(tab.status_label.textFormat(), Qt.TextFormat.PlainText)

    def test_edits_during_save_remain_unsaved(self):
        client, tab = self.make_tab(status="configured")
        client.save_delay = 0.15
        field = tab._controls[("researcher", "files_accessed")]
        field["spin"].setValue(12)
        tab._save()
        field["spin"].setValue(13)
        self.assertTrue(wait_for(self.app, lambda: not tab._request_in_flight))
        self.assertEqual(field["spin"].value(), 13)
        self.assertTrue(tab._dirty)
        self.assertIn("Newer edits remain unsaved.", tab.status_label.text())

    def test_reset_confirmation_and_worker_thread_request(self):
        client, tab = self.make_tab(status="configured")
        with patch(
            "desktop_app.role_baseline_setup_tab.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            tab._confirm_reset()
        self.assertTrue(wait_for(self.app, lambda: client.reset_calls == 1))
        self.assertTrue(wait_for(self.app, lambda: not tab._request_in_flight))
        self.assertTrue(wait_for(self.app, lambda: not tab._configured))
        self.assertTrue(all(not field["edited"] for field in tab._controls.values()))
        self.assertTrue(all(
            field["exact"] == field["default"] for field in tab._controls.values()
        ))
        self.assertNotEqual(client.call_threads[-1], threading.get_ident())

    def test_server_text_uses_plain_labels_and_warning_appears_next_to_field(self):
        client, tab = self.make_tab(
            status="configured",
            role_name="<b>researcher</b>",
            label_override="<script>metric</script>",
        )
        title = next(
            label for label in tab.findChildren(QLabel)
            if label.text() == "<script>metric</script>"
        )
        self.assertEqual(title.textFormat(), Qt.TextFormat.PlainText)
        first_role = "<b>researcher</b>"
        field = tab._controls[(first_role, "data_transferred_mb")]
        field["spin"].setValue(121)
        self.assertFalse(field["warning"].isHidden())
        self.assertEqual(field["warning"].textFormat(), Qt.TextFormat.PlainText)
        self.assertTrue(client.call_threads)

    def test_admin_baseline_banner_and_badge_visibility(self):
        window = MainWindow.__new__(MainWindow)
        QMainWindow.__init__(window)
        window.client = type("Client", (), {"role": "admin"})()
        window.baseline_banner = QLabel(
            "Role baselines are not configured. InSighter is using built-in defaults."
        )
        window.stack = QStackedWidget()
        window.stack.addWidget(QWidget())
        window.stack.addWidget(QWidget())
        window.stack.setCurrentIndex(0)
        window.baseline_nav_item = _NavItem("●", "Baseline Setup")
        window.baseline_nav_item.badge = plain_label("SETUP", window.baseline_nav_item)
        window._apply_baseline_status({"status": "not_configured"})
        self.assertFalse(window.baseline_banner.isHidden())
        self.assertFalse(window.baseline_nav_item.badge.isHidden())
        window.stack.setCurrentIndex(1)
        window._set_baseline_banner_visibility()
        self.assertTrue(window.baseline_banner.isHidden())
        self.assertFalse(window.baseline_nav_item.badge.isHidden())
        window.client.role = "management"
        window._set_baseline_banner_visibility()
        self.assertTrue(window.baseline_banner.isHidden())
        self.assertTrue(window.baseline_nav_item.badge.isHidden())
        window._apply_baseline_status({"status": "configured"})
        self.assertTrue(window.baseline_nav_item.badge.isHidden())


class BaselineApiClientTest(unittest.TestCase):
    def test_baseline_helpers_use_request_and_preserve_server_errors(self):
        client = ApiClient()

        class Response:
            def __init__(self, status_code, body):
                self.status_code = status_code
                self.body = body

            def json(self):
                return self.body

        with patch.object(client, "_request", return_value=Response(200, {"status": "ok"})) as request:
            self.assertEqual(client.get_baseline_setup(), {"status": "ok"})
            request.assert_called_with("GET", "/api/baseline-setup")
            self.assertEqual(
                client.save_baseline_setup({"values": []}),
                {"status": "ok"},
            )
            request.assert_called_with("PUT", "/api/baseline-setup", json={"values": []})
            self.assertEqual(client.reset_baseline_setup(), {"status": "ok"})
            request.assert_called_with("POST", "/api/baseline-setup/reset", json={})

        with patch.object(client, "_request", return_value=Response(400, {"error": "Bad baseline"})):
            with self.assertRaises(ApiError) as caught:
                client.save_baseline_setup({"values": []})
        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(caught.exception.message, "Bad baseline")


if __name__ == "__main__":
    unittest.main()
