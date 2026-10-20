import logging

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QDoubleSpinBox,
    QSpinBox, QCheckBox, QLineEdit, QPushButton, QGridLayout, QComboBox
)
from PyQt6.QtCore import QTimer, pyqtSignal
from desktop_app.async_utils import run_async
from desktop_app.safe_text import plain_label

logger = logging.getLogger(__name__)


class UavConfigTab(QWidget):
    configuration_saved = pyqtSignal()

    """
    Lets the IT administrator configure organization-specific parameters
    for edge deployment before the Raspberry Pi 5 is deployed onboard the
    UAV: detection thresholds, log collection targets, sync intervals,
    and a reference name for the drone operator. (Chapter 3, Presentation Tier spec.)
    """

    LOG_SOURCES = [
        ("active_directory", "Active Directory"),
        ("google_workspace", "Google Workspace"),
        ("microsoft_365", "Microsoft 365"),
    ]

    def __init__(self, client):
        super().__init__()
        self.client = client
        self._refreshing = True
        self._dirty = False
        self._edit_revision = 0
        self._config_revision = 0
        self._save_revision = 0
        self._refresh_pending = False
        self._build_ui()
        self._refreshing = False

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(16)

        title = QLabel("Deployment Configuration")
        title.setObjectName("h1")
        outer.addWidget(title)
        subtitle = QLabel(
            "Sector, detection thresholds, monitoring scope, and deployment configuration."
        )
        subtitle.setObjectName("muted")
        outer.addWidget(subtitle)

        card = QFrame()
        card.setObjectName("card")
        form = QGridLayout(card)
        form.setContentsMargins(20, 20, 20, 20)
        form.setVerticalSpacing(14)
        form.setHorizontalSpacing(16)

        row = 0
        form.addWidget(self._label("Organizational Sector"), row, 0)
        self.sector = QComboBox()
        self.sector_options = [
            ("private_school", "Private School"),
            ("uav_disaster_response", "UAV Disaster Response"),
        ]
        for value, label in self.sector_options:
            self.sector.addItem(label, value)
        self.sector.currentIndexChanged.connect(self._sector_changed)
        form.addWidget(self.sector, row, 1)
        row += 1

        form.addWidget(self._label("Allowed Roles"), row, 0)
        self.roles_label = plain_label("-")
        self.roles_label.setWordWrap(True)
        form.addWidget(self.roles_label, row, 1)
        row += 1

        form.addWidget(self._label("Monitored Data Categories"), row, 0)
        self.categories_label = plain_label("-")
        self.categories_label.setWordWrap(True)
        form.addWidget(self.categories_label, row, 1)
        row += 1

        form.addWidget(self._label("Detection Threshold — High Risk"), row, 0)
        self.threshold_high = QDoubleSpinBox()
        self.threshold_high.setRange(0, 100)
        self.threshold_high.setSuffix(" %")
        self.threshold_high.valueChanged.connect(self._mark_dirty)
        form.addWidget(self.threshold_high, row, 1)
        row += 1

        form.addWidget(self._label("Detection Threshold — Medium Risk"), row, 0)
        self.threshold_medium = QDoubleSpinBox()
        self.threshold_medium.setRange(0, 100)
        self.threshold_medium.setSuffix(" %")
        self.threshold_medium.valueChanged.connect(self._mark_dirty)
        form.addWidget(self.threshold_medium, row, 1)
        row += 1

        self.sync_label = self._label("Sync Interval")
        form.addWidget(self.sync_label, row, 0)
        self.sync_interval = QSpinBox()
        self.sync_interval.setRange(1, 1440)
        self.sync_interval.setSuffix(" min")
        self.sync_interval.valueChanged.connect(self._mark_dirty)
        form.addWidget(self.sync_interval, row, 1)
        row += 1

        form.addWidget(self._label("Log Collection Targets"), row, 0)
        targets_row = QHBoxLayout()
        self.target_checks = {}
        for key, display in self.LOG_SOURCES:
            cb = QCheckBox(display)
            cb.toggled.connect(self._mark_dirty)
            self.target_checks[key] = cb
            targets_row.addWidget(cb)
        targets_widget = QWidget()
        targets_widget.setLayout(targets_row)
        form.addWidget(targets_widget, row, 1)
        row += 1

        self.drone_username_label = self._label("Drone Operator Username")
        form.addWidget(self.drone_username_label, row, 0)
        self.drone_username = QLineEdit()
        self.drone_username.textEdited.connect(self._mark_dirty)
        form.addWidget(self.drone_username, row, 1)
        row += 1

        self.drone_username_help = QLabel(
            "Reference name only. Creating a login for this operator is not yet supported."
        )
        self.drone_username_help.setObjectName("muted")
        self.drone_username_help.setWordWrap(True)
        form.addWidget(self.drone_username_help, row, 1)
        row += 1

        outer.addWidget(card)

        self.scope_label = plain_label("Monitoring scope: -")
        self.scope_label.setObjectName("muted")
        outer.addWidget(self.scope_label)

        btn_row = QHBoxLayout()
        self.save_btn = QPushButton("Save Configuration")
        self.save_btn.setObjectName("primary")
        self.save_btn.clicked.connect(self._save)
        btn_row.addWidget(self.save_btn)
        self.reset_btn = QPushButton("Reset")
        self.reset_btn.clicked.connect(self._reset)
        btn_row.addWidget(self.reset_btn)
        self.unsaved_label = QLabel("Unsaved changes")
        self.unsaved_label.setObjectName("muted")
        self.unsaved_label.setVisible(False)
        btn_row.addWidget(self.unsaved_label)
        self.status_label = plain_label("")
        self.status_label.setObjectName("muted")
        btn_row.addWidget(self.status_label)
        btn_row.addStretch()
        outer.addLayout(btn_row)

        self.updated_label = plain_label("")
        self.updated_label.setObjectName("muted")
        outer.addWidget(self.updated_label)
        outer.addStretch()
        self._sector_changed()

    def _label(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet("color:#94a3b8; font-size:12px;")
        return lbl

    def refresh(self, force=False):
        config_revision = self._config_revision
        def fetch():
            try:
                config = self.client.get_uav_config()
            except Exception as exc:
                config = exc
            try:
                scope = self.client.get_monitoring_scope()
            except Exception as exc:
                scope = exc
            return config, scope

        worker = run_async(
            fetch,
            lambda result: self._apply_refresh(result, config_revision),
            lambda exc: self._refresh_failed(exc),
            parent=self,
            key="refresh",
            report_success=False,
        )
        if worker is None:
            if force:
                self._refresh_pending = True
            return
        self._refresh_pending = False

    def _apply_refresh(self, result, config_revision):
        cfg, scope = result
        if not isinstance(cfg, Exception) and config_revision == self._config_revision:
            self.updated_label.setText(f"Last updated: {cfg['updated_at']}")
            if not self._dirty:
                self._apply_config(cfg)

        if isinstance(scope, Exception):
            self.scope_label.setText("Monitoring scope: unavailable")
        else:
            self.scope_label.setText(
                f"Monitoring scope: {scope['in_scope_percent']:.1f}% of events in scope "
                f"({scope['in_scope_events']}/{scope['total_events']})"
            )
        monitor = getattr(self.client, "connection_monitor", None)
        failures = [value for value in (cfg, scope) if isinstance(value, Exception)]
        if monitor is not None:
            if failures:
                monitor.record_failure(failures[0], True, "UavConfigTab")
            else:
                monitor.record_success("UavConfigTab")
        else:
            for failure in failures:
                logger.warning("Failed to refresh UAV configuration: %s", failure)
        self._start_pending_refresh()

    def _refresh_failed(self, exc):
        self._start_pending_refresh()

    def _start_pending_refresh(self):
        if self._refresh_pending:
            self._refresh_pending = False
            QTimer.singleShot(0, lambda: self.refresh(force=True))

    def _apply_config(self, cfg):
        self._refreshing = True
        try:
            index = self.sector.findData(cfg.get("sector", "private_school"))
            self.sector.setCurrentIndex(index if index >= 0 else 0)
            self._set_taxonomy(cfg.get("taxonomy", {}))
            self.threshold_high.setValue(cfg["threshold_high"])
            self.threshold_medium.setValue(cfg["threshold_medium"])
            self.sync_interval.setValue(cfg["sync_interval_minutes"])
            self.drone_username.setText(cfg["drone_operator_username"])
            active_targets = set(cfg["log_targets"].split(","))
            for key, cb in self.target_checks.items():
                cb.setChecked(key in active_targets)
        finally:
            self._refreshing = False
        self._update_dirty_indicator()

    def _save(self):
        payload = {
            "sector": self.sector.currentData(),
            "threshold_high": self.threshold_high.value(),
            "threshold_medium": self.threshold_medium.value(),
            "sync_interval_minutes": self.sync_interval.value(),
            "log_targets": ",".join(k for k, cb in self.target_checks.items() if cb.isChecked()),
            "drone_operator_username": self.drone_username.text().strip() or "drone_operator",
        }
        if not self.save_btn.isEnabled():
            return
        self._save_revision = self._edit_revision
        self.save_btn.setEnabled(False)
        self.status_label.setText("")
        run_async(
            lambda: self.client.save_deployment_config(payload),
            self._save_succeeded,
            self._save_failed,
            parent=self,
            monitor_source="uav-config-save",
            counts_toward_offline=False,
            report_success=False,
        )

    def _save_succeeded(self, _result):
        self.save_btn.setEnabled(True)
        self._config_revision += 1
        if self._edit_revision == self._save_revision:
            self._dirty = False
        self._update_dirty_indicator()
        message = "Configuration saved."
        if self._dirty:
            message += " Newer edits remain unsaved."
        self.status_label.setText(message)
        self.status_label.setStyleSheet("color:#22c55e; font-size:12px;")
        self.configuration_saved.emit()
        self.refresh(force=True)

    def _save_failed(self, exc):
        self.save_btn.setEnabled(True)
        message = exc.message if hasattr(exc, "message") else str(exc)
        self.status_label.setText(f"Failed to save: {message}")
        self.status_label.setStyleSheet("color:#ef4444; font-size:12px;")

    def _reset(self):
        self._dirty = False
        self._update_dirty_indicator()
        self.status_label.setText("")
        self.refresh(force=True)

    def _mark_dirty(self, *_args):
        if not self._refreshing:
            self._dirty = True
            self._edit_revision += 1
            self._update_dirty_indicator()

    def _update_dirty_indicator(self):
        self.unsaved_label.setVisible(self._dirty)

    def _set_taxonomy(self, taxonomy):
        self.roles_label.setText(", ".join(taxonomy.get("roles", [])) or "-")
        self.categories_label.setText(", ".join(taxonomy.get("data_categories", [])) or "-")

    def _sector_changed(self):
        sector = self.sector.currentData()
        self._mark_dirty()
        labels = dict(self.sector_options)
        mappings = {
            "private_school": ["researcher", "intern", "admin_staff", "it_staff"],
            "uav_disaster_response": ["drone_platform_access", "disaster_data"],
        }
        roles = {
            "private_school": ["researcher", "intern", "admin_staff", "it_staff"],
            "uav_disaster_response": ["drone_operator"],
        }
        self._set_taxonomy({"roles": roles.get(sector, []), "data_categories": mappings.get(sector, [])})
        is_uav = sector == "uav_disaster_response"
        for widget in (
            self.sync_interval, self.drone_username, self.drone_username_help,
            self.sync_label, self.drone_username_label,
        ):
            widget.setVisible(is_uav)
