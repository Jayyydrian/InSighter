from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QScrollArea, QPushButton
from PyQt6.QtCore import Qt
from PyQt6 import sip

from desktop_app.ai_worker import AiAnalysisWorker

SEVERITY_COLORS = {"CRITICAL": "#ef4444", "HIGH": "#ef4444", "MEDIUM": "#f59e0b"}


class AlertsTab(QWidget):
    def __init__(self, client):
        super().__init__()
        self.client = client
        self._ai_workers = set()
        self._ai_results = {}
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(12)

        title = QLabel("Alerts")
        title.setObjectName("h1")
        outer.addWidget(title)
        subtitle = QLabel("All flagged activity from the current detection window.")
        subtitle.setObjectName("muted")
        outer.addWidget(subtitle)

        self.card = QFrame()
        self.card.setObjectName("card")
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(16, 12, 16, 12)
        dot = QLabel("●")
        dot.setStyleSheet("color:#ef4444; font-size:9px;")
        header.addWidget(dot)
        header.addWidget(QLabel("Alert Feed"))
        header.addStretch()
        self.count_label = QLabel("0 alerts")
        self.count_label.setObjectName("muted")
        header.addWidget(self.count_label)
        card_layout.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        self.container = QWidget()
        self.list_layout = QVBoxLayout(self.container)
        self.list_layout.setSpacing(0)
        self.list_layout.addStretch()
        scroll.setWidget(self.container)
        card_layout.addWidget(scroll)
        outer.addWidget(self.card)

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
        layout = QHBoxLayout()
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(10)

        line = QFrame()
        line.setFixedWidth(2)
        line.setMinimumHeight(36)
        line.setStyleSheet(f"background:{color}; border-radius:1px;")
        layout.addWidget(line)

        body = QVBoxLayout()
        body.setSpacing(2)
        user_label = QLabel(f"<b>{a['user']}</b>  ·  login@{a['login_hour']}:00")
        body.addWidget(user_label)

        desc = QLabel(a["description"])
        desc.setWordWrap(True)
        body.addWidget(desc)

        meta = QLabel(f"Flagged by: {a['flagged_by']}  ·  Blended score: {a['final_score']}")
        meta.setObjectName("muted")
        body.addWidget(meta)

        ai_button = QPushButton("AI Analysis")
        ai_button.setObjectName("secondary")
        ai_button.setFixedWidth(110)
        ai_panel = QLabel("")
        ai_panel.setWordWrap(True)
        ai_panel.setObjectName("muted")
        ai_panel.setVisible(False)
        ai_button.clicked.connect(
            lambda: self._request_ai_analysis(a["id"], ai_button, ai_panel)
        )
        body.addWidget(ai_button)
        body.addWidget(ai_panel)
        self._render_ai_state(a["id"], ai_button, ai_panel)
        layout.addLayout(body, stretch=1)

        sev = QLabel(a["severity"])
        sev.setStyleSheet(
            f"color:{color}; background:rgba(239,68,68,30); border:1px solid {color}; "
            "border-radius:4px; padding:2px 8px; font-size:10px; font-weight:700;"
        )
        layout.addWidget(sev, alignment=Qt.AlignmentFlag.AlignTop)
        card.setLayout(layout)

        return card

    def _request_ai_analysis(self, alert_id, button, panel):
        if self._ai_results.get(alert_id, {}).get("status") == "loading":
            return
        self._ai_results[alert_id] = {"status": "loading"}
        self._render_ai_state(alert_id, button, panel)
        worker = AiAnalysisWorker(self.client, alert_id)
        self._ai_workers.add(worker)
        worker.result_ready.connect(
            lambda result: self._show_ai_result(alert_id, result, button, panel)
        )
        worker.finished.connect(lambda: self._ai_workers.discard(worker))
        worker.start()

    def _show_ai_result(self, alert_id, result, button, panel):
        self._ai_results[alert_id] = {
            "status": "done" if result.get("available") else "error",
            **result,
        }
        if sip.isdeleted(button) or sip.isdeleted(panel):
            return
        self._render_ai_state(alert_id, button, panel)

    def _render_ai_state(self, alert_id, button, panel):
        entry = self._ai_results.get(alert_id)
        if not entry:
            button.setEnabled(True)
            button.setText("AI Analysis")
            panel.setVisible(False)
            return
        button.setEnabled(entry["status"] != "loading")
        button.setText("Analyzing..." if entry["status"] == "loading" else "Refresh AI Analysis")
        panel.setVisible(True)
        if entry["status"] == "loading":
            panel.setText("Asking the local AI model...")
        elif entry.get("available"):
            panel.setText(
                f"What this means: {entry.get('explanation')}\n"
                f"Recommended action: {entry.get('recommendation')}"
            )
        else:
            panel.setText(
                f"AI analysis unavailable: {entry.get('reason', 'Ollama is not running.')}"
            )

    def _recommended_action(self, severity):
        return {
            "CRITICAL": "Immediately review session and consider temporary account suspension pending investigation.",
            "HIGH": "Escalate to IT security lead for manual review within 24 hours.",
            "MEDIUM": "Monitor for recurrence; no immediate action required.",
        }.get(severity, "Monitor for recurrence.")
