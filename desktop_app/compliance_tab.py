from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

SEVERITY_COLORS = {"CRITICAL": "#ef4444", "HIGH": "#ef4444", "MEDIUM": "#f59e0b"}


class ComplianceTab(QWidget):
    """Flagged incidents and audit records with pseudonymized identities."""

    def __init__(self, client):
        super().__init__()
        self.client = client
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(12)

        title = QLabel("Compliance Audit")
        title.setObjectName("h1")
        outer.addWidget(title)
        subtitle = QLabel(
            "Flagged behavior and access events with pseudonymized user identities."
        )
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        outer.addWidget(subtitle)

        outer.addWidget(self._build_alert_panel())
        outer.addWidget(self._build_audit_panel())

    def _build_alert_panel(self):
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(16, 12, 16, 12)
        dot = QLabel("●")
        dot.setStyleSheet("color:#ef4444; font-size:9px;")
        header.addWidget(dot)
        header.addWidget(QLabel("Alert Feed"))
        header.addStretch()
        self.alert_count = QLabel("0 alerts")
        self.alert_count.setObjectName("muted")
        header.addWidget(self.alert_count)
        layout.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(190)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        self.alert_container = QWidget()
        self.alert_layout = QVBoxLayout(self.alert_container)
        self.alert_layout.setSpacing(0)
        self.alert_layout.addStretch()
        scroll.setWidget(self.alert_container)
        layout.addWidget(scroll)
        return card

    def _build_audit_panel(self):
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        header.setContentsMargins(16, 12, 16, 12)
        header.addWidget(QLabel("●"))
        header.addWidget(QLabel("Access Audit Trail"))
        header.addStretch()
        self.audit_count = QLabel("0 entries")
        self.audit_count.setObjectName("muted")
        header.addWidget(self.audit_count)
        layout.addLayout(header)

        self.audit_table = QTableWidget(0, 4)
        self.audit_table.setHorizontalHeaderLabels(["Timestamp", "Pseudonymous User", "Action", "Detail"])
        self.audit_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.audit_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.audit_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.audit_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.audit_table.verticalHeader().setVisible(False)
        self.audit_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.audit_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        layout.addWidget(self.audit_table)
        return card

    def refresh(self):
        try:
            alerts = self.client.get_compliance_alerts()
            audit_entries = self.client.get_compliance_audit_log()
        except Exception:
            return
        self._refresh_alerts(alerts)
        self._refresh_audit(audit_entries)

    def _refresh_alerts(self, alerts):
        self.alert_count.setText(f"{len(alerts)} alerts")
        while self.alert_layout.count():
            item = self.alert_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not alerts:
            empty = QLabel("No anomalous activity detected in the current window.")
            empty.setObjectName("muted")
            self.alert_layout.addWidget(empty)
        else:
            for alert in alerts:
                self.alert_layout.addWidget(self._alert_item(alert))
        self.alert_layout.addStretch()

    def _alert_item(self, alert):
        color = SEVERITY_COLORS.get(alert["severity"], "#f59e0b")
        card = QFrame()
        layout = QHBoxLayout(card)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(10)

        line = QFrame()
        line.setFixedWidth(2)
        line.setMinimumHeight(36)
        line.setStyleSheet(f"background:{color}; border-radius:1px;")
        layout.addWidget(line)

        body = QVBoxLayout()
        body.setSpacing(2)
        body.addWidget(QLabel(f"<b>{alert['user']}</b>  ·  login@{alert['login_hour']}:00"))
        description = QLabel(alert["description"])
        description.setWordWrap(True)
        body.addWidget(description)
        meta = QLabel(
            f"Flagged by: {alert['flagged_by']}  ·  Blended score: {alert['final_score']}"
        )
        meta.setObjectName("muted")
        body.addWidget(meta)
        layout.addLayout(body, stretch=1)

        severity = QLabel(alert["severity"])
        severity.setStyleSheet(
            f"color:{color}; background:rgba(239,68,68,30); border:1px solid {color}; "
            "border-radius:4px; padding:2px 8px; font-size:10px; font-weight:700;"
        )
        layout.addWidget(severity, alignment=Qt.AlignmentFlag.AlignTop)
        return card

    def _refresh_audit(self, entries):
        self.audit_count.setText(f"{len(entries)} entries")
        self.audit_table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            values = [
                entry["timestamp"],
                entry["username"] or "-",
                entry["action"],
                entry["detail"] or "",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column != 3:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.audit_table.setItem(row, column, item)
