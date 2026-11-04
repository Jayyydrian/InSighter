import json

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame, QHeaderView, QHBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from desktop_app.async_utils import run_async


class EndpointActivityTab(QWidget):
    """USB connection and path-free file operation counts from endpoint sensors."""

    def __init__(self, client):
        super().__init__()
        self.client = client
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel("USB & File Activity")
        title.setObjectName("h1")
        layout.addWidget(title)

        subtitle = QLabel(
            "Removable-volume events and file operation counts; file names and paths are not collected."
        )
        subtitle.setObjectName("muted")
        layout.addWidget(subtitle)

        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        header = QHBoxLayout()
        header.setContentsMargins(16, 12, 16, 12)
        header.addWidget(QLabel("●"))
        header.addWidget(QLabel("Endpoint Activity"))
        header.addStretch()
        self.count_label = QLabel("—")
        self.count_label.setObjectName("muted")
        header.addWidget(self.count_label)
        card_layout.addLayout(header)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Timestamp (UTC)", "Endpoint", "User", "Event", "Drive", "Operation Counts"]
        )
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        card_layout.addWidget(self.table)
        layout.addWidget(card)

    def refresh(self):
        run_async(
            self.client.get_endpoint_activity,
            self._apply_refresh,
            parent=self,
            key="refresh",
        )

    def _apply_refresh(self, events):
        self.count_label.setText(f"{len(events)} events")
        self.table.setRowCount(len(events))
        for row, event in enumerate(events):
            operation_counts = event.get("operation_counts", {})
            if isinstance(operation_counts, str):
                try:
                    operation_counts = json.loads(operation_counts)
                except json.JSONDecodeError:
                    operation_counts = {}
            if isinstance(operation_counts, dict):
                operation_text = ", ".join(
                    f"{name}: {count}"
                    for name, count in sorted(operation_counts.items())
                )
            else:
                operation_text = ""
            values = (
                event.get("timestamp", ""),
                event.get("endpoint_id", ""),
                event.get("user", ""),
                event.get("event_type", ""),
                event.get("drive_letter", ""),
                operation_text,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, column, item)
