from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QHeaderView, QFrame
)
from PyQt6.QtCore import Qt


class AuditTab(QWidget):
    """
    Privacy-Compliant Audit Mode (Chapter 3): accountability trail for
    Data Privacy Act (R.A. 10173) compliance -- who accessed or changed
    what, and when.
    """

    def __init__(self, client):
        super().__init__()
        self.client = client
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel("Audit Log")
        title.setObjectName("h1")
        layout.addWidget(title)

        subtitle = QLabel(
            "Privacy-Compliant Audit Mode -- accountability trail for Data Privacy Act (R.A. 10173) compliance."
        )
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        panel_header = QHBoxLayout()
        panel_header.setContentsMargins(16, 12, 16, 12)
        panel_header.addWidget(QLabel("●"))
        panel_header.addWidget(QLabel("Recent Activity"))
        panel_header.addStretch()
        self.count_label = QLabel("—")
        self.count_label.setObjectName("muted")
        panel_header.addWidget(self.count_label)
        card_layout.addLayout(panel_header)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Timestamp", "User", "Action", "Detail"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        card_layout.addWidget(self.table)
        layout.addWidget(card)

    def refresh(self):
        try:
            entries = self.client.get_audit_log()
        except Exception:
            return

        self.count_label.setText(f"{len(entries)} entries")
        self.table.setRowCount(len(entries))
        for row, e in enumerate(entries):
            values = [e["timestamp"], e["username"] or "—", e["action"], e["detail"] or ""]
            for col, val in enumerate(values):
                item = QTableWidgetItem(val)
                if col != 3:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, col, item)
