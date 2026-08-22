from collections import defaultdict

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QTableWidget,
    QTableWidgetItem, QHeaderView, QScrollArea
)
from PyQt6.QtCore import Qt


ROLE_COLORS = {
    "it admin": "#3b82f6",
    "hr officer": "#8b5cf6",
    "developer": "#06b6d4",
    "registrar": "#10b981",
    "finance analyst": "#ef4444",
}


class RoleSessionsTab(QWidget):
    """User risk matrices grouped into one table per behavioral role."""

    def __init__(self, client):
        super().__init__()
        self.client = client
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Role-Based User Risk")
        title.setObjectName("h1")
        header.addWidget(title)
        header.addStretch()
        self.count_label = QLabel("")
        self.count_label.setObjectName("muted")
        header.addWidget(self.count_label)
        outer.addLayout(header)

        subtitle = QLabel("User risk grouped by role, from lowest to highest risk.")
        subtitle.setObjectName("muted")
        outer.addWidget(subtitle)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(14)
        scroll.setWidget(self.content)
        outer.addWidget(scroll)

    def refresh(self):
        try:
            scores = self.client.get_scores()
        except Exception:
            return

        grouped = defaultdict(list)
        for user in scores:
            grouped[user.get("role", "unknown") or "unknown"].append(user)

        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        total = sum(len(rows) for rows in grouped.values())
        self.count_label.setText(f"{total} users · {len(grouped)} roles")
        for role in sorted(grouped):
            rows = sorted(grouped[role], key=lambda row: (row["risk_score"], row["user"]))
            self.content_layout.addWidget(self._role_table(role, rows))
        self.content_layout.addStretch()

    def _role_table(self, role, rows):
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(16, 12, 16, 12)
        role_label = QLabel(role.title())
        role_label.setStyleSheet(
            f"color:{ROLE_COLORS.get(role, '#e2e8f0')}; font-weight:700; font-size:13px;"
        )
        header.addWidget(role_label)
        header.addStretch()
        header.addWidget(QLabel(f"{len(rows)} users"))
        layout.addLayout(header)

        table = QTableWidget(len(rows), 6)
        table.setHorizontalHeaderLabels(
            ["User", "Risk Score", "Level", "Role Baseline", "Avg Files", "Transfer (MB)"]
        )
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        table.setShowGrid(False)

        for row_index, user in enumerate(rows):
            values = [
                user["user"], f'{user["risk_score"]}%', user["risk_level"],
                f'{user.get("role_baseline_score", 0)}', str(user["avg_files"]),
                f'{user["avg_transfer"]} MB',
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                table.setItem(row_index, column, item)

        layout.addWidget(table)
        return card
