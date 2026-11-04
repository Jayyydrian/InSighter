import json

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QHeaderView, QFrame
)
from PyQt6.QtCore import Qt

from desktop_app.async_utils import run_async

class SessionsTab(QWidget):
    """Raw session/log entries as ingested, most recent first."""

    def __init__(self, client):
        super().__init__()
        self.client = client
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel("User Sessions")
        title.setObjectName("h1")
        layout.addWidget(title)

        subtitle = QLabel("Raw session/log entries as ingested, most recent first.")
        subtitle.setObjectName("muted")
        layout.addWidget(subtitle)

        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        panel_header = QHBoxLayout()
        panel_header.setContentsMargins(16, 12, 16, 12)
        panel_header.addWidget(QLabel("●"))
        panel_header.addWidget(QLabel("Session Log"))
        panel_header.addStretch()
        self.count_label = QLabel("—")
        self.count_label.setObjectName("muted")
        panel_header.addWidget(self.count_label)
        card_layout.addLayout(panel_header)

        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels(
            ["ID", "User", "Role", "Source", "Login Hour", "Files", "Transfer (MB)", "Failed Logins", "Off-Hours", "Usable Features"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        card_layout.addWidget(self.table)
        layout.addWidget(card)

    def refresh(self):
        run_async(
            self.client.get_sessions,
            self._apply_refresh,
            parent=self,
            key="refresh",
        )

    def _apply_refresh(self, sessions):
        self.count_label.setText(f"{len(sessions)} sessions")
        self.table.setRowCount(len(sessions))
        for row, s in enumerate(sessions):
            status = s.get("feature_status")
            if isinstance(status, str):
                try:
                    status = json.loads(status)
                except json.JSONDecodeError:
                    status = {}
            if not isinstance(status, dict):
                status = {}
            feature_values = {
                "login_hour": s.get("login_hour"),
                "files_accessed": s.get("files_accessed"),
                "data_transferred_mb": s.get("data_transferred_mb"),
                "failed_logins": s.get("failed_logins"),
                "off_hours_access": s.get("off_hours_access"),
            }
            usable_features = sum(
                status.get(name) == "supported"
                if status
                else value is not None
                for name, value in feature_values.items()
            )
            score_state = "scored" if usable_features == len(feature_values) else "not scored"
            values = [
                str(s["id"]), s["user"], s.get("role", "unknown"), s.get("source", "synthetic"),
                f'{s["login_hour"]}:00' if s.get("login_hour") is not None else "Unavailable",
                str(s["files_accessed"]) if s.get("files_accessed") is not None else "Unavailable",
                str(s["data_transferred_mb"]) if s.get("data_transferred_mb") is not None else "Unavailable",
                str(s["failed_logins"]) if s.get("failed_logins") is not None else "Unavailable",
                (
                    "Yes" if s.get("off_hours_access")
                    else "No" if s.get("off_hours_access") is not None
                    else "Unavailable"
                ),
                f"{usable_features}/5; {score_state}",
            ]
            for col, val in enumerate(values):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, col, item)
