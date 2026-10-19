from math import ceil, floor

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from desktop_app.async_utils import run_async
from desktop_app.safe_text import plain_label


HELP_TEXT = (
    "These values set the reference for the role-deviation signal, one component "
    "of the risk score. The IsolationForest and One-Class SVM detectors are not changed."
)

DISPLAY_UNITS = {
    "files_accessed": {"scale": 1, "unit": "files per event", "step": 1},
    "data_transferred_mb": {"scale": 1, "unit": "MB per event", "step": 5},
    "failed_logins": {
        "scale": 100,
        "unit": "failed logins per 100 events",
        "step": 5,
    },
    "off_hours_access": {"scale": 100, "unit": "% of events", "step": 1},
}


class RoleBaselineSetupTab(QWidget):
    configuration_changed = pyqtSignal()

    def __init__(self, client):
        super().__init__()
        self.client = client
        self._request_in_flight = False
        self._refresh_pending = False
        self._refreshing = False
        self._dirty = False
        self._edit_revision = 0
        self._save_revision = 0
        self._configured = False
        self._step = 0
        self._payload = None
        self._roles = []
        self._controls = {}
        self._saved_values = {}
        self._pages_initialized = False
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(14)

        title = QLabel("Role Baseline Setup")
        title.setObjectName("h1")
        outer.addWidget(title)

        self.help_label = plain_label(HELP_TEXT)
        self.help_label.setObjectName("muted")
        self.help_label.setWordWrap(True)
        outer.addWidget(self.help_label)

        self.status_label = plain_label("Loading role baselines...")
        self.status_label.setObjectName("muted")
        outer.addWidget(self.status_label)

        self.sector_label = plain_label("")
        self.sector_label.setObjectName("muted")
        outer.addWidget(self.sector_label)

        self.step_label = plain_label("")
        self.step_label.setObjectName("muted")
        outer.addWidget(self.step_label)

        self.pages = QStackedWidget()
        outer.addWidget(self.pages)

        actions = QHBoxLayout()
        self.back_button = QPushButton("Back")
        self.back_button.clicked.connect(self._back)
        actions.addWidget(self.back_button)
        self.next_button = QPushButton("Next")
        self.next_button.clicked.connect(self._next)
        actions.addWidget(self.next_button)
        self.finish_button = QPushButton("Finish setup")
        self.finish_button.setObjectName("primary")
        self.finish_button.clicked.connect(self._finish_setup)
        actions.addWidget(self.finish_button)
        self.save_button = QPushButton("Save")
        self.save_button.setObjectName("primary")
        self.save_button.clicked.connect(self._save)
        actions.addWidget(self.save_button)
        self.discard_button = QPushButton("Discard")
        self.discard_button.clicked.connect(self._discard)
        actions.addWidget(self.discard_button)
        self.reset_button = QPushButton("Reset to defaults")
        self.reset_button.clicked.connect(self._confirm_reset)
        actions.addWidget(self.reset_button)

        self.unsaved_label = plain_label("Unsaved changes")
        self.unsaved_label.setObjectName("muted")
        actions.addWidget(self.unsaved_label)
        actions.addStretch()
        outer.addLayout(actions)
        self._update_actions()

    def refresh(self):
        if self._request_in_flight:
            self._refresh_pending = True
            return
        self._refresh_pending = False
        self._request_in_flight = True
        worker = run_async(
            self.client.get_baseline_setup,
            self._apply_refresh,
            self._refresh_failed,
            parent=self,
            key="baseline-refresh",
            monitor_source="baseline-setup-refresh",
            report_success=False,
        )
        if worker is None:
            self._request_in_flight = False
            self._refresh_pending = True

    def _apply_refresh(self, payload):
        self._request_in_flight = False
        self._payload = payload
        self._configured = payload.get("status") == "configured"
        self.sector_label.setText("Sector: " + str(payload.get("sector") or "unknown"))
        if not self._pages_initialized:
            self._apply_payload(payload)
        else:
            self._update_payload_metadata(payload)
        self._start_pending_refresh()

    def _apply_payload(self, payload):
        self._refreshing = True
        try:
            self._roles = payload.get("roles", [])
            self._controls = {}
            self._saved_values = {}
            self._clear_pages()
            for role_data in self._roles:
                page = self._build_role_page(role_data)
                self.pages.addWidget(page)
            if not self._configured:
                self.pages.addWidget(self._build_review_page())
            self._pages_initialized = True
            self.pages.setCurrentIndex(self._step)
            self._saved_values = self._current_values()
            self._dirty = False
            state = "Configured" if self._configured else "Not configured"
            self.status_label.setText("Status: " + state)
            self._show_step()
            self._update_actions()
        finally:
            self._refreshing = False

    def _update_payload_metadata(self, payload):
        self._roles = payload.get("roles", self._roles)
        for role_data in self._roles:
            role = str(role_data.get("role", ""))
            for item in role_data.get("features", []):
                key = (role, str(item.get("feature", "")))
                field = self._controls.get(key)
                if field is None:
                    continue
                default = item.get("default_value")
                observed = item.get("observed", {}).get("mean")
                field["default"] = 0.0 if default is None else float(default)
                field["observed"] = None if observed is None else float(observed)
                field["default_label"].setText(
                    f"Built-in default: "
                    f"{self._format_display_value(field['default'], field['scale'])} "
                    f"{field['display_unit']}"
                )
                observed_text = (
                    "-" if field["observed"] is None
                    else self._format_display_value(field["observed"], field["scale"])
                )
                field["observed_label"].setText(
                    f"Observed mean: {observed_text} {field['display_unit']} "
                    "(for reference only)"
                )
                self._refresh_field_state(key)

        review_exists = self.pages.count() > len(self._roles)
        if not self._configured and not review_exists:
            self.pages.addWidget(self._build_review_page())
        elif not self._configured and review_exists:
            self._replace_review_page()
        elif self._configured and review_exists:
            review_page = self.pages.widget(self.pages.count() - 1)
            self.pages.removeWidget(review_page)
            review_page.deleteLater()
        if self._step >= self.pages.count():
            self._step = max(0, self.pages.count() - 1)
        self.pages.setCurrentIndex(self._step)
        self._show_step()
        self._update_actions()

    def _clear_pages(self):
        while self.pages.count():
            page = self.pages.widget(0)
            self.pages.removeWidget(page)
            page.deleteLater()

    def _build_role_page(self, role_data):
        page = QWidget()
        layout = QVBoxLayout(page)
        role = str(role_data.get("role", ""))
        title = plain_label(role)
        title.setObjectName("h2")
        layout.addWidget(title)

        defaults_button = QPushButton("Use defaults")
        defaults_button.clicked.connect(lambda _checked=False, role_name=role: self._use_defaults(role_name))
        layout.addWidget(defaults_button, alignment=Qt.AlignmentFlag.AlignRight)

        for item in role_data.get("features", []):
            layout.addWidget(self._build_feature_row(role, item))
        layout.addStretch()
        return page

    def _build_feature_row(self, role, item):
        feature = str(item.get("feature", ""))
        key = (role, feature)
        card = QFrame()
        card.setObjectName("card")
        row = QHBoxLayout(card)
        labels = QVBoxLayout()

        title = plain_label(item.get("label", feature))
        title.setStyleSheet("font-size:13px; font-weight:600; color:#e2e8f0;")
        labels.addWidget(title)
        display = DISPLAY_UNITS.get(
            feature,
            {"scale": 1, "unit": str(item.get("unit", "")), "step": 1},
        )
        scale = display["scale"]
        display_unit = display["unit"]
        unit = plain_label(display_unit)
        unit.setObjectName("muted")
        labels.addWidget(unit)

        default = item.get("default_value")
        default_value = 0.0 if default is None else float(default)
        default_label = plain_label(
            f"Built-in default: {self._format_display_value(default_value, scale)} "
            f"{display_unit}"
        )
        labels.addWidget(default_label)
        observed = item.get("observed", {}).get("mean")
        observed_value = None if observed is None else float(observed)
        observed_text = (
            "-" if observed_value is None
            else self._format_display_value(observed_value, scale)
        )
        observed_label = plain_label(
            f"Observed mean: {observed_text} {display_unit} (for reference only)"
        )
        observed_label.setObjectName("muted")
        labels.addWidget(observed_label)

        customized = plain_label("Customized")
        customized.setStyleSheet("color:#fbbf24; font-weight:600;")
        customized.setVisible(False)
        labels.addWidget(customized)

        warning = plain_label("")
        warning.setWordWrap(True)
        warning.setStyleSheet("color:#fbbf24;")
        warning.setVisible(False)
        labels.addWidget(warning)
        row.addLayout(labels, stretch=1)

        spin = QDoubleSpinBox()
        minimum = ceil(float(item.get("min", 0)) * scale)
        maximum = floor(float(item.get("max", 100)) * scale)
        spin.setRange(minimum, maximum)
        spin.setDecimals(0)
        spin.setSingleStep(display["step"])
        declared = item.get("declared_value")
        initial = item.get("effective_value") if self._configured and declared is not None else default
        initial_value = 0.0 if initial is None else float(initial)
        spin.setValue(self._display_value(initial_value, scale))
        spin.valueChanged.connect(lambda value, k=key: self._value_changed(k, value))
        spin.lineEdit().textEdited.connect(
            lambda _text, k=key: self._mark_user_edit_pending(k)
        )
        spin.editingFinished.connect(lambda k=key: self._commit_user_text_edit(k))
        row.addWidget(spin)
        self._controls[key] = {
            "spin": spin,
            "default": 0.0 if default is None else float(default),
            "exact": initial_value,
            "edited": False,
            "user_edit_pending": False,
            "scale": scale,
            "display_unit": display_unit,
            "observed": observed_value,
            "customized": customized,
            "warning": warning,
            "default_label": default_label,
            "observed_label": observed_label,
            "unit": display_unit,
            "label": str(item.get("label", feature)),
            "role": role,
            "feature": feature,
        }
        self._refresh_field_state(key)
        return card

    def _build_review_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("Review baselines")
        title.setObjectName("h2")
        layout.addWidget(title)
        for role_data in self._roles:
            role = str(role_data.get("role", ""))
            heading = plain_label(role)
            heading.setStyleSheet("font-size:13px; font-weight:600; color:#e2e8f0;")
            layout.addWidget(heading)
            for item in role_data.get("features", []):
                key = (role, str(item.get("feature", "")))
                field = self._controls.get(key)
                if field is None:
                    continue
                default = field["default"]
                line = (
                    f"{field['label']} — chosen: "
                    f"{self._format_display_value(field['exact'], field['scale'])} "
                    f"{field['unit']}; default: "
                    f"{self._format_display_value(default, field['scale'])} "
                    f"{field['unit']}"
                )
                layout.addWidget(plain_label(line))
                warning_text = self._warning_for(field, field["exact"])
                if warning_text:
                    warning_label = plain_label(warning_text)
                    warning_label.setStyleSheet("color:#fbbf24;")
                    layout.addWidget(warning_label)
        layout.addStretch()
        return page

    def _use_defaults(self, role):
        self._refreshing = True
        try:
            for key, field in self._controls.items():
                if key[0] == role:
                    field["exact"] = field["default"]
                    field["edited"] = False
                    field["user_edit_pending"] = False
                    field["spin"].setValue(
                        self._display_value(field["exact"], field["scale"])
                    )
        finally:
            self._refreshing = False
        self._edit_revision += 1
        self._dirty = self._current_values() != self._saved_values
        for key in self._controls:
            self._refresh_field_state(key)
        self._update_actions()

    def _value_changed(self, key, _value):
        if self._refreshing:
            return
        field = self._controls[key]
        field["exact"] = float(_value) / field["scale"]
        field["edited"] = True
        self._edit_revision += 1
        self._dirty = self._current_values() != self._saved_values
        self._refresh_field_state(key)
        self._update_actions()

    def _mark_user_edit_pending(self, key):
        if not self._refreshing:
            self._controls[key]["user_edit_pending"] = True

    def _commit_user_text_edit(self, key):
        field = self._controls[key]
        if self._refreshing or not field["user_edit_pending"]:
            return
        field["user_edit_pending"] = False
        # Typing the displayed rounded default is still an explicit user edit.
        field["exact"] = field["spin"].value() / field["scale"]
        field["edited"] = True
        self._edit_revision += 1
        self._dirty = self._current_values() != self._saved_values
        self._refresh_field_state(key)
        self._update_actions()

    @staticmethod
    def _display_value(value, scale):
        return round(float(value) * scale)

    @staticmethod
    def _format_display_value(value, scale):
        display_value = round(float(value) * scale)
        if value > 0 and display_value == 0:
            display_value = 1
        return str(display_value)

    def _refresh_field_state(self, key):
        field = self._controls.get(key)
        if field is None:
            return
        current = field["exact"]
        field["customized"].setVisible(
            self._configured and abs(current - field["default"]) > 1e-9
        )
        message = self._warning_for(field, current)
        field["warning"].setText(message or "")
        field["warning"].setVisible(bool(message))

    @staticmethod
    def _warning_for(field, value):
        observed = field["observed"]
        if observed is None:
            return None
        if value > observed * 5:
            return "Declared value is more than 5x the observed mean per event."
        if value < observed / 5:
            return "Declared value is less than one-fifth of the observed mean per event."
        return None

    def _current_values(self):
        return {key: field["exact"] for key, field in self._controls.items()}

    def _payload_values(self):
        return [
            {"role": role, "feature": feature, "value": value}
            for (role, feature), value in self._current_values().items()
        ]

    def _back(self):
        if self._step > 0:
            self._step -= 1
            self.pages.setCurrentIndex(self._step)
            self._show_step()
            self._update_actions()

    def _next(self):
        if self._step < self.pages.count() - 1:
            self._step += 1
            if not self._configured and self._step == len(self._roles):
                self._replace_review_page()
            self.pages.setCurrentIndex(self._step)
            self._show_step()
            self._update_actions()

    def _replace_review_page(self):
        old = self.pages.widget(self.pages.count() - 1)
        self.pages.removeWidget(old)
        old.deleteLater()
        self.pages.addWidget(self._build_review_page())

    def _show_step(self):
        if not self._roles:
            self.step_label.setText("")
            return
        if not self._configured and self._step == len(self._roles):
            self.step_label.setText("Review")
        else:
            self.step_label.setText(
                f"Role {self._step + 1} of {len(self._roles)}"
            )

    def _update_actions(self):
        saving = self._request_in_flight
        setup = not self._configured
        review = setup and self._step == len(self._roles) and bool(self._roles)
        self.back_button.setVisible(bool(self._roles) and (self._step > 0 or self._configured))
        self.back_button.setEnabled(not saving and self._step > 0)
        self.next_button.setVisible(bool(self._roles) and not review)
        self.next_button.setEnabled(not saving and self._step < self.pages.count() - 1)
        self.finish_button.setVisible(review)
        self.finish_button.setEnabled(review and not saving)
        self.save_button.setVisible(self._configured)
        self.save_button.setEnabled(self._configured and self._dirty and not saving)
        self.discard_button.setVisible(self._configured)
        self.discard_button.setEnabled(self._configured and self._dirty and not saving)
        self.reset_button.setVisible(self._configured)
        self.reset_button.setEnabled(self._configured and not saving)
        self.unsaved_label.setVisible(self._dirty)

    def _save(self):
        if self._request_in_flight or not self._dirty:
            return
        self._submit(complete=True)

    def _finish_setup(self):
        if self._request_in_flight or self._configured:
            return
        answer = QMessageBox.question(
            self,
            "Finish role baseline setup",
            "Finish setup and apply these role baselines?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._submit(complete=True)

    def _submit(self, complete):
        values = self._payload_values()
        self._save_revision = self._edit_revision
        self._request_in_flight = True
        self.status_label.setText("Saving role baselines...")
        self._update_actions()
        worker = run_async(
            lambda: self.client.save_baseline_setup(
                {"values": values, "complete": complete}
            ),
            lambda result: self._save_succeeded(result, values),
            self._save_failed,
            parent=self,
            key="baseline-save",
            monitor_source="baseline-setup-save",
            counts_toward_offline=False,
            report_success=False,
        )
        if worker is None:
            self._request_in_flight = False
            self.status_label.setText("A baseline request is already in progress.")
            self._update_actions()

    def _save_succeeded(self, _result, saved_values):
        self._request_in_flight = False
        self._saved_values = {
            (entry["role"], entry["feature"]): float(entry["value"])
            for entry in saved_values
        }
        self._configured = True
        self._dirty = self._current_values() != self._saved_values
        self.status_label.setText(
            "Role baselines saved." if not self._dirty
            else "Role baselines saved. Newer edits remain unsaved."
        )
        self._status_style(success=True)
        if self.pages.count() > len(self._roles):
            review_page = self.pages.widget(self.pages.count() - 1)
            self.pages.removeWidget(review_page)
            review_page.deleteLater()
        self._step = min(self._step, max(0, len(self._roles) - 1))
        self._show_step()
        for key in self._controls:
            self._refresh_field_state(key)
        self._update_actions()
        self.configuration_changed.emit()
        self.refresh()

    def _save_failed(self, exc):
        self._request_in_flight = False
        message = getattr(exc, "message", None) or str(exc)
        self.status_label.setText("Failed to save: " + message)
        self._status_style(success=False)
        self._dirty = self._current_values() != self._saved_values
        self._update_actions()
        self._start_pending_refresh()

    def _discard(self):
        if self._request_in_flight:
            return
        self._refreshing = True
        try:
            for key, field in self._controls.items():
                saved_value = self._saved_values.get(key, field["default"])
                field["exact"] = saved_value
                field["edited"] = False
                field["user_edit_pending"] = False
                field["spin"].setValue(
                    self._display_value(saved_value, field["scale"])
                )
            self._dirty = False
            self._edit_revision += 1
        finally:
            self._refreshing = False
        for key in self._controls:
            self._refresh_field_state(key)
        self.status_label.setText("Edits discarded.")
        self._status_style(success=True)
        self._update_actions()

    def _confirm_reset(self):
        if self._request_in_flight:
            return
        answer = QMessageBox.question(
            self,
            "Reset role baselines",
            "Reset this sector to built-in defaults? The setup status will return to Not configured.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._request_in_flight = True
        self.status_label.setText("Resetting role baselines...")
        self._update_actions()
        worker = run_async(
            self.client.reset_baseline_setup,
            self._reset_succeeded,
            self._reset_failed,
            parent=self,
            key="baseline-reset",
            monitor_source="baseline-setup-reset",
            counts_toward_offline=False,
            report_success=False,
        )
        if worker is None:
            self._request_in_flight = False
            self.status_label.setText("A baseline request is already in progress.")
            self._update_actions()

    def _reset_succeeded(self, _result):
        self._request_in_flight = False
        self._dirty = False
        self._step = 0
        self._refreshing = True
        try:
            for key, field in self._controls.items():
                field["exact"] = field["default"]
                field["edited"] = False
                field["user_edit_pending"] = False
                field["spin"].setValue(
                    self._display_value(field["exact"], field["scale"])
                )
            self._saved_values = self._current_values()
        finally:
            self._refreshing = False
        for key in self._controls:
            self._refresh_field_state(key)
        self.status_label.setText("Role baselines reset to built-in defaults.")
        self._status_style(success=True)
        self.configuration_changed.emit()
        self.refresh()

    def _reset_failed(self, exc):
        self._request_in_flight = False
        message = getattr(exc, "message", None) or str(exc)
        self.status_label.setText("Failed to reset: " + message)
        self._status_style(success=False)
        self._update_actions()
        self._start_pending_refresh()

    def _refresh_failed(self, exc):
        self._request_in_flight = False
        message = getattr(exc, "message", None) or str(exc)
        self.status_label.setText("Role baselines unavailable: " + message)
        self._status_style(success=False)
        self._start_pending_refresh()

    def _start_pending_refresh(self):
        if self._refresh_pending and not self._request_in_flight:
            self._refresh_pending = False
            self.refresh()

    def _status_style(self, success):
        self.status_label.setStyleSheet(
            "color:#22c55e;" if success else "color:#ef4444;"
        )
