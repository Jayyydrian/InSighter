from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QPushButton

STATUS_COLORS = {"not_connected": "#94a3b8", "connected": "#6ee7b7"}


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

        action_row = QHBoxLayout()
        self.ingest_btn = QPushButton("Ingest Events")
        self.ingest_btn.setObjectName("primary")
        self.ingest_btn.clicked.connect(self._ingest)
        action_row.addWidget(self.ingest_btn)
        self.ingest_status = QLabel("")
        self.ingest_status.setObjectName("muted")
        action_row.addWidget(self.ingest_status)
        action_row.addStretch()
        outer.addLayout(action_row)

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
                f"color:{color}; background:rgba(100,116,139,35); border:1px solid {color}; border-radius:10px; "
                f"padding:4px 12px; font-size:10px; font-weight:700;"
            )
            row_layout.addWidget(pill)

            self.card_layout.addWidget(row)

    def _ingest(self):
        self.ingest_btn.setEnabled(False)
        self.ingest_status.setText("Fetching events...")
        try:
            result = self.client.ingest_events()
            self.ingest_status.setText(f"Inserted {result.get('inserted', 0)} events.")
            self.refresh()
        except Exception as exc:
            self.ingest_status.setText(str(exc))
        finally:
            self.ingest_btn.setEnabled(True)
