from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QListWidget,
    QListWidgetItem, QGridLayout
)
from PyQt6.QtCore import Qt

RISK_COLORS = {"HIGH": "#ef4444", "MEDIUM": "#f59e0b", "LOW": "#22c55e"}


class ProfilesTab(QWidget):
    """
    Shows the compiled behavioral baseline for each monitored employee,
    alongside their current session deviations -- i.e. why they received
    the risk score they did. (Chapter 3, Presentation Tier spec.)
    """

    def __init__(self, client):
        super().__init__()
        self.client = client
        self.users = []
        self.baseline = {}
        self._build_ui()

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        # Left: user list
        left = QVBoxLayout()

        title = QLabel("User Behavior Profiles")
        title.setObjectName("h1")
        left.addWidget(title)

        self.user_list = QListWidget()
        self.user_list.setFixedWidth(220)
        self.user_list.currentRowChanged.connect(self._on_select)
        left.addWidget(self.user_list)

        left_wrap = QWidget()
        left_wrap.setLayout(left)
        left_wrap.setFixedWidth(240)
        layout.addWidget(left_wrap)

        # Right: detail card
        self.detail_card = QFrame()
        self.detail_card.setObjectName("card")

        self.detail_layout = QVBoxLayout(self.detail_card)
        self.detail_layout.setContentsMargins(24, 24, 24, 24)

        placeholder = QLabel("Select a user to view their behavioral baseline.")
        placeholder.setObjectName("muted")
        self.detail_layout.addWidget(placeholder)

        layout.addWidget(self.detail_card, stretch=1)

    def refresh(self):
        try:
            self.users = self.client.get_scores()
        except Exception:
            return

        normal_users = [u for u in self.users if u["risk_level"] == "LOW"] or self.users

        self.baseline = {
            "avg_files": round(sum(u["avg_files"] for u in normal_users) / len(normal_users), 1),
            "avg_transfer": round(sum(u["avg_transfer"] for u in normal_users) / len(normal_users), 1),
            "avg_failed_logins": round(sum(u["avg_failed_logins"] for u in normal_users) / len(normal_users), 1),
            "off_hours": round(sum(u["off_hours"] for u in normal_users) / len(normal_users), 1),
        }

        current_username = (
            self.user_list.currentItem().text()
            if self.user_list.currentItem()
            else None
        )

        self.user_list.clear()

        for u in self.users:
            self.user_list.addItem(QListWidgetItem(u["user"]))

        if current_username:
            for i in range(self.user_list.count()):
                if self.user_list.item(i).text() == current_username:
                    self.user_list.setCurrentRow(i)
                    break

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)

            if item.widget():
                item.widget().deleteLater()

            elif item.layout():
                self._clear_layout(item.layout())
                item.layout().deleteLater()

    def _on_select(self, row):
        self._clear_layout(self.detail_layout)

        if row < 0 or row >= len(self.users):
            placeholder = QLabel("Select a user to view their behavioral baseline.")
            placeholder.setObjectName("muted")
            self.detail_layout.addWidget(placeholder)
            return

        u = self.users[row]
        color = RISK_COLORS.get(u["risk_level"], "#e2e8f0")

        header = QHBoxLayout()

        name = QLabel(u["user"])
        name.setObjectName("h1")

        badge = QLabel(u["risk_level"])
        badge.setStyleSheet(
            f"color:{color}; border:1px solid {color}; border-radius:10px;"
            f"padding:3px 10px; font-size:11px; font-weight:700;"
        )

        score = QLabel(f"{u['risk_score']}% risk score")
        score.setStyleSheet(f"color:{color}; font-weight:700;")

        header.addWidget(name)
        header.addWidget(badge)
        header.addStretch()
        header.addWidget(score)

        self.detail_layout.addLayout(header)

        subtitle = QLabel(
            f"Role: {u.get('role', 'unknown').title()} · "
            f"Isolation Forest: {u['if_score']} · One-Class SVM: {u['ocsvm_score']} · "
            f"Role baseline deviation: {u.get('role_baseline_score', 0)}"
        )
        subtitle.setObjectName("muted")
        self.detail_layout.addWidget(subtitle)
        self.detail_layout.addSpacing(16)

        title = QLabel("BASELINE (NORMAL) vs. CURRENT SESSION DEVIATION")
        title.setObjectName("h2")
        self.detail_layout.addWidget(title)

        grid = QGridLayout()
        grid.setSpacing(10)

        rows = [
            ("Avg. Files Accessed", self.baseline["avg_files"], u["avg_files"]),
            ("Avg. Data Transferred (MB)", self.baseline["avg_transfer"], u["avg_transfer"]),
            ("Avg. Failed Logins", self.baseline["avg_failed_logins"], u["avg_failed_logins"]),
            ("Off-Hours Access Events", self.baseline["off_hours"], u["off_hours"]),
        ]

        grid.addWidget(self._th("Metric"), 0, 0)
        grid.addWidget(self._th("Baseline (Normal)"), 0, 1)
        grid.addWidget(self._th("This User"), 0, 2)
        grid.addWidget(self._th("Deviation"), 0, 3)

        for i, (label, baseline_val, user_val) in enumerate(rows, start=1):
            deviation = user_val - baseline_val
            dev_pct = (deviation / baseline_val * 100) if baseline_val else 0

            grid.addWidget(QLabel(label), i, 0)
            grid.addWidget(QLabel(str(baseline_val)), i, 1)
            grid.addWidget(QLabel(str(user_val)), i, 2)

            dev_label = QLabel(f"{'+' if deviation >= 0 else ''}{dev_pct:.0f}%")

            dev_color = (
                "#ef4444"
                if dev_pct > 50
                else "#f59e0b"
                if dev_pct > 15
                else "#22c55e"
            )

            dev_label.setStyleSheet(
                f"color:{dev_color}; font-weight:700;"
            )

            grid.addWidget(dev_label, i, 3)

        self.detail_layout.addLayout(grid)
        self.detail_layout.addStretch()

    def _th(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet(
            "color:#64748b; font-size:11px; font-weight:700; text-transform:uppercase;"
        )
        return lbl