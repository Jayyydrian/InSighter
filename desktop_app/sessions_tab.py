from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QHeaderView, QFrame
)
from PyQt6.QtCore import Qt


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

        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(
            ["ID", "User", "Role", "Source", "Login Hour", "Files", "Transfer (MB)", "Failed Logins", "Off-Hours"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        card_layout.addWidget(self.table)
        layout.addWidget(card)

    def refresh(self):
        try:
            sessions = self.client.get_sessions()
        except Exception:
            return

        self.count_label.setText(f"{len(sessions)} sessions")
        self.table.setRowCount(len(sessions))
        for row, s in enumerate(sessions):
            values = [
                str(s["id"]), s["user"], s.get("role", "unknown"), s.get("source", "synthetic"),
                f'{s["login_hour"]}:00',
                str(s["files_accessed"]), f'{s["data_transferred_mb"]}',
                str(s["failed_logins"]), "Yes" if s["off_hours_access"] else "No",
            ]
            for col, val in enumerate(values):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, col, item)
