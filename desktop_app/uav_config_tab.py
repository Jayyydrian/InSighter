from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QDoubleSpinBox,
    QSpinBox, QCheckBox, QLineEdit, QPushButton, QGridLayout
)


class UavConfigTab(QWidget):
    """
    Lets the IT administrator configure organization-specific parameters
    for edge deployment before the Raspberry Pi 5 is deployed onboard the
    UAV: detection thresholds, log collection targets, sync intervals,
    and Drone Operator RBAC credentials. (Chapter 3, Presentation Tier spec.)
    """

    LOG_SOURCES = [
        ("active_directory", "Active Directory"),
        ("google_workspace", "Google Workspace"),
        ("microsoft_365", "Microsoft 365"),
    ]

    def __init__(self, client):
        super().__init__()
        self.client = client
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(16)

        title = QLabel("UAV Configuration Module")
        title.setObjectName("h1")
        outer.addWidget(title)
        subtitle = QLabel(
            "Parameters pushed to the Edge Inference Engine (Raspberry Pi 5) prior to deployment."
        )
        subtitle.setObjectName("muted")
        outer.addWidget(subtitle)

        card = QFrame()
        card.setObjectName("card")
        form = QGridLayout(card)
        form.setContentsMargins(24, 24, 24, 24)
        form.setVerticalSpacing(14)
        form.setHorizontalSpacing(16)

        row = 0
        form.addWidget(self._label("Detection Threshold — High Risk"), row, 0)
        self.threshold_high = QDoubleSpinBox()
        self.threshold_high.setRange(0, 100)
        self.threshold_high.setSuffix(" %")
        form.addWidget(self.threshold_high, row, 1)
        row += 1

        form.addWidget(self._label("Detection Threshold — Medium Risk"), row, 0)
        self.threshold_medium = QDoubleSpinBox()
        self.threshold_medium.setRange(0, 100)
        self.threshold_medium.setSuffix(" %")
        form.addWidget(self.threshold_medium, row, 1)
        row += 1

        form.addWidget(self._label("Sync Interval"), row, 0)
        self.sync_interval = QSpinBox()
        self.sync_interval.setRange(1, 1440)
        self.sync_interval.setSuffix(" min")
        form.addWidget(self.sync_interval, row, 1)
        row += 1

        form.addWidget(self._label("Log Collection Targets"), row, 0)
        targets_row = QHBoxLayout()
        self.target_checks = {}
        for key, display in self.LOG_SOURCES:
            cb = QCheckBox(display)
            self.target_checks[key] = cb
            targets_row.addWidget(cb)
        targets_widget = QWidget()
        targets_widget.setLayout(targets_row)
        form.addWidget(targets_widget, row, 1)
        row += 1

        form.addWidget(self._label("Drone Operator Username"), row, 0)
        self.drone_username = QLineEdit()
        form.addWidget(self.drone_username, row, 1)
        row += 1

        form.addWidget(self._label("Drone Operator Password"), row, 0)
        pw_row = QHBoxLayout()
        self.drone_password = QLineEdit()
        self.drone_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.drone_password.setPlaceholderText("Leave blank to keep unchanged")
        pw_row.addWidget(self.drone_password)
        pw_widget = QWidget()
        pw_widget.setLayout(pw_row)
        form.addWidget(pw_widget, row, 1)
        row += 1

        outer.addWidget(card)

        btn_row = QHBoxLayout()
        self.save_btn = QPushButton("Save Configuration")
        self.save_btn.setObjectName("primary")
        self.save_btn.clicked.connect(self._save)
        btn_row.addWidget(self.save_btn)
        self.status_label = QLabel("")
        self.status_label.setObjectName("muted")
        btn_row.addWidget(self.status_label)
        btn_row.addStretch()
        outer.addLayout(btn_row)

        self.updated_label = QLabel("")
        self.updated_label.setObjectName("muted")
        outer.addWidget(self.updated_label)
        outer.addStretch()

    def _label(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet("color:#94a3b8; font-size:12px;")
        return lbl

    def refresh(self):
        try:
            cfg = self.client.get_uav_config()
        except Exception:
            return

        self.threshold_high.setValue(cfg["threshold_high"])
        self.threshold_medium.setValue(cfg["threshold_medium"])
        self.sync_interval.setValue(cfg["sync_interval_minutes"])
        self.drone_username.setText(cfg["drone_operator_username"])

        active_targets = set(cfg["log_targets"].split(","))
        for key, cb in self.target_checks.items():
            cb.setChecked(key in active_targets)

        self.updated_label.setText(f"Last updated: {cfg['updated_at']}")

    def _save(self):
        payload = {
            "threshold_high": self.threshold_high.value(),
            "threshold_medium": self.threshold_medium.value(),
            "sync_interval_minutes": self.sync_interval.value(),
            "log_targets": ",".join(k for k, cb in self.target_checks.items() if cb.isChecked()),
            "drone_operator_username": self.drone_username.text().strip() or "drone_operator",
        }
        try:
            self.client.save_uav_config(payload)
            self.status_label.setText("Saved.")
            self.status_label.setStyleSheet("color:#22c55e; font-size:12px;")
            self.drone_password.clear()
            self.refresh()
        except Exception as exc:
            self.status_label.setText(f"Failed to save: {exc}")
            self.status_label.setStyleSheet("color:#ef4444; font-size:12px;")
