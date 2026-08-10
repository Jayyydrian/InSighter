from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame


class IntegrationsTab(QWidget):
    def __init__(self, client):
        super().__init__()
        self.client = client
        self.card_layout = QVBoxLayout()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        title = QLabel("Integrations")
        title.setObjectName("h1")
        outer.addWidget(title)
        subtitle = QLabel("Status of the supported log-ingestion data sources.")
        subtitle.setObjectName("muted")
        outer.addWidget(subtitle)
        card = QFrame()
        card.setObjectName("card")
        card.setLayout(self.card_layout)
        outer.addWidget(card)
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
        for integration in integrations:
            row = QHBoxLayout()
            text = QVBoxLayout()
            name = QLabel(integration.get("name", ""))
            name.setStyleSheet("font-weight:600;")
            note = QLabel(integration.get("note", ""))
            note.setObjectName("muted")
            note.setWordWrap(True)
            text.addWidget(name)
            text.addWidget(note)
            row.addLayout(text)
            row.addStretch()
            status = QLabel(integration.get("status", "unknown").replace("_", " ").upper())
            row.addWidget(status)
            container = QWidget()
            container.setLayout(row)
            self.card_layout.addWidget(container)
