from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QHeaderView
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

        header = QHBoxLayout()
        title = QLabel("User Sessions")
        title.setObjectName("h1")
        header.addWidget(title)
        header.addStretch()
        self.count_label = QLabel("")
        self.count_label.setObjectName("muted")
        header.addWidget(self.count_label)
        layout.addLayout(header)

        subtitle = QLabel("Raw session/log entries as ingested, most recent first.")
        subtitle.setObjectName("muted")
        layout.addWidget(subtitle)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["ID", "User", "Login Hour", "Files", "Transfer (MB)", "Failed Logins", "Off-Hours"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.table)

    def refresh(self):
        try:
            sessions = self.client.get_sessions()
        except Exception:
            return

        self.count_label.setText(f"{len(sessions)} sessions")
        self.table.setRowCount(len(sessions))
        for row, s in enumerate(sessions):
            values = [
                str(s["id"]), s["user"], f'{s["login_hour"]}:00',
                str(s["files_accessed"]), f'{s["data_transferred_mb"]}',
                str(s["failed_logins"]), "Yes" if s["off_hours_access"] else "No",
            ]
            for col, val in enumerate(values):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, col, item)
