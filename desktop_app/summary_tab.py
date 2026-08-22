from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame

RISK_COLORS = {"HIGH": "#ef4444", "MEDIUM": "#f59e0b", "LOW": "#10b981"}


def _metric_card(label_text, color):
    card = QFrame()
    card.setObjectName("card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(20, 18, 20, 18)
    label = QLabel(label_text)
    label.setObjectName("h2")
    value = QLabel("—")
    value.setStyleSheet(f"color:{color}; font-size:34px; font-weight:700; font-family:Consolas,monospace;")
    layout.addWidget(label)
    layout.addWidget(value)
    card.value_label = value
    return card


class SummaryTab(QWidget):
    """Aggregate-only view for the 'management' RBAC role -- no individual
    user data, per Chapter 3's access-control design."""

    def __init__(self, client):
        super().__init__()
        self.client = client
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title = QLabel("Summary Risk Report")
        title.setObjectName("h1")
        layout.addWidget(title)
        subtitle = QLabel(
            "Aggregate view only. Individual user activity is restricted to IT administrator accounts."
        )
        subtitle.setObjectName("muted")
        layout.addWidget(subtitle)

        cards_row = QHBoxLayout()
        self.card_high = _metric_card("High Risk Users", RISK_COLORS["HIGH"])
        self.card_medium = _metric_card("Medium Risk Users", RISK_COLORS["MEDIUM"])
        self.card_low = _metric_card("Low Risk Users", RISK_COLORS["LOW"])
        for c in (self.card_high, self.card_medium, self.card_low):
            cards_row.addWidget(c)
        layout.addLayout(cards_row)
        layout.addStretch()

    def refresh(self):
        try:
            counts = self.client.get_summary()
        except Exception:
            return
        self.card_high.value_label.setText(str(counts.get("HIGH", 0)))
        self.card_medium.value_label.setText(str(counts.get("MEDIUM", 0)))
        self.card_low.value_label.setText(str(counts.get("LOW", 0)))
