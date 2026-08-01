from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QScrollArea

SEVERITY_COLORS = {"CRITICAL": "#ef4444", "HIGH": "#ef4444", "MEDIUM": "#f59e0b"}


class AlertsTab(QWidget):
    def __init__(self, client):
        super().__init__()
        self.client = client
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(12)

        title = QLabel("Plain-Language Alerts")
        title.setObjectName("h1")
        outer.addWidget(title)
        subtitle = QLabel("Flagged activity, translated into non-technical summaries with recommended actions.")
        subtitle.setObjectName("muted")
        outer.addWidget(subtitle)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        self.container = QWidget()
        self.list_layout = QVBoxLayout(self.container)
        self.list_layout.setSpacing(10)
        self.list_layout.addStretch()
        scroll.setWidget(self.container)
        outer.addWidget(scroll)

    def refresh(self):
        try:
            alerts = self.client.get_alerts()
        except Exception:
            return

        # clear existing cards
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not alerts:
            empty = QLabel("No anomalous activity detected in the current window.")
            empty.setObjectName("muted")
            self.list_layout.addWidget(empty)
        else:
            for a in alerts:
                self.list_layout.addWidget(self._alert_card(a))

        self.list_layout.addStretch()

    def _alert_card(self, a):
        color = SEVERITY_COLORS.get(a["severity"], "#f59e0b")
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)

        top = QHBoxLayout()
        user_label = QLabel(f"<b>{a['user']}</b>  ·  login@{a['login_hour']}:00")
        top.addWidget(user_label)
        top.addStretch()
        sev = QLabel(a["severity"])
        sev.setStyleSheet(f"color:{color}; font-weight:700; font-size:11px;")
        top.addWidget(sev)
        layout.addLayout(top)

        desc = QLabel(a["description"])
        desc.setWordWrap(True)
        layout.addWidget(desc)

        meta = QLabel(f"Flagged by: {a['flagged_by']}  ·  Blended score: {a['final_score']}")
        meta.setObjectName("muted")
        layout.addWidget(meta)

        action = self._recommended_action(a["severity"])
        rec = QLabel(f"Recommended action: {action}")
        rec.setStyleSheet("color:#94a3b8; font-size:12px; font-style:italic;")
        layout.addWidget(rec)

        return card

    def _recommended_action(self, severity):
        return {
            "CRITICAL": "Immediately review session and consider temporary account suspension pending investigation.",
            "HIGH": "Escalate to IT security lead for manual review within 24 hours.",
            "MEDIUM": "Monitor for recurrence; no immediate action required.",
        }.get(severity, "Monitor for recurrence.")
