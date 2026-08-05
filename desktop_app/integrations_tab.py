from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame

STATUS_COLORS = {"not_connected": "#64748b", "connected": "#22c55e"}


class IntegrationsTab(QWidget):
    """Status of the three supported log-ingestion data sources."""

    def __init__(self, client):
        super().__init__()
        self.client = client
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(12)

        title = QLabel("Integrations")
        title.setObjectName("h1")
        outer.addWidget(title)
        subtitle = QLabel("Status of the three supported log-ingestion data sources.")
        subtitle.setObjectName("muted")
        outer.addWidget(subtitle)

        self.card = QFrame()
        self.card.setObjectName("card")
        self.card_layout = QVBoxLayout(self.card)
        self.card_layout.setContentsMargins(0, 0, 0, 0)
        self.card_layout.setSpacing(0)
        outer.addWidget(self.card)
        outer.addStretch()

    def refresh(self):
        try:
            integrations = self.client.get_integrations()
        except Exception:
            return

        while self.card_layout.count():
            item = self.card_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for i, integ in enumerate(integrations):
            row = QFrame()
            if i < len(integrations) - 1:
                row.setStyleSheet("border-bottom: 1px solid #1e2d45;")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(18, 14, 18, 14)

            text_col = QVBoxLayout()
            name = QLabel(integ["name"])
            name.setStyleSheet("font-size:13px; font-weight:600; color:#e2e8f0;")
            note = QLabel(integ["note"])
            note.setObjectName("muted")
            note.setWordWrap(True)
            text_col.addWidget(name)
            text_col.addWidget(note)
            row_layout.addLayout(text_col, stretch=1)

            color = STATUS_COLORS.get(integ["status"], "#64748b")
            pill = QLabel(integ["status"].replace("_", " ").upper())
            pill.setStyleSheet(
                f"color:{color}; border:1px solid {color}; border-radius:10px; "
                f"padding:4px 12px; font-size:10px; font-weight:700;"
            )
            row_layout.addWidget(pill)

            self.card_layout.addWidget(row)
