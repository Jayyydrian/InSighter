"""
Overview dashboard -- a 1:1 port of the web POC's dashboard.html "Overview"
section: stat row, live User Risk Matrix, Alert Feed, Risk History
sparklines, and Runtime Resources panel.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QTableWidget, QTableWidgetItem, QHeaderView, QScrollArea
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
import zlib

RED = "#ef4444"
YELLOW = "#f59e0b"
GREEN = "#10b981"
ACCENT = "#3b82f6"
MUTED = "#64748b"
MUTED2 = "#94a3b8"
BORDER = "#1e2d45"
BORDER2 = "#253550"
SURFACE = "#111827"
TEXT = "#e2e8f0"
CRITICAL = "#dc2626"

RISK_COLORS = {"HIGH": "#ef4444", "MEDIUM": "#f59e0b", "LOW": "#10b981"}
SEVERITY_COLORS = {"CRITICAL": CRITICAL, "HIGH": RED, "MEDIUM": YELLOW}

# Users and roles are whatever the active sector/roster returns from the API
# (see /api/scores), not a fixed cast -- so avatar colors are derived from the
# username instead of looked up in a static per-demo-user table.
AVATAR_PALETTE = ["#3b82f6", "#8b5cf6", "#06b6d4", "#10b981", "#ef4444", "#f59e0b", "#ec4899", "#14b8a6"]

HISTORY_MAX = 20


def avatar_color(username):
    # Stable across process restarts (unlike builtin hash(), which is salted).
    return AVATAR_PALETTE[zlib.crc32(username.encode()) % len(AVATAR_PALETTE)]


def score_color(pct):
    if pct >= 40:
        return RED
    if pct >= 15:
        return YELLOW
    return GREEN


def gauge_color(val):
    if val >= 80:
        return RED
    if val >= 60:
        return YELLOW
    return ACCENT


def fmt_uptime(s):
    try:
        s = int(s)
    except (TypeError, ValueError):
        return "-"
    h, m = s // 3600, (s % 3600) // 60
    return f"{h}h {m}m"


def _panel(title_text, dot_color, meta_text=""):
    """A .panel -- bordered card with a header row (dot + title, meta on the right)."""
    panel = QFrame()
    panel.setObjectName("card")
    outer = QVBoxLayout(panel)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)

    head = QFrame()
    head.setStyleSheet(f"border: none; border-bottom: 1px solid {BORDER};")
    head_layout = QHBoxLayout(head)
    head_layout.setContentsMargins(16, 12, 16, 12)

    dot = QLabel("\u25cf")
    dot.setStyleSheet(f"color:{dot_color}; font-size:9px;")
    title = QLabel(title_text)
    title.setStyleSheet(f"color:{TEXT}; font-weight:600; font-size:12px;")
    head_layout.addWidget(dot)
    head_layout.addWidget(title)
    head_layout.addStretch()

    meta = QLabel(meta_text)
    meta.setStyleSheet(f"color:{MUTED}; font-size:10px;")
    head_layout.addWidget(meta)

    outer.addWidget(head)
    return panel, outer, meta


class DashboardTab(QWidget):
    """The Overview page: stat row, risk matrix + alert feed, sparklines + resources."""

    def __init__(self, client):
        super().__init__()
        self.client = client
        self.history = {}
        self.last_users = []
        self._sparkline_rows = {}
        self._build_ui()

    # -- UI construction --------------------------------------------------
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(16)

        outer.addLayout(self._build_stat_row())

        row1 = QHBoxLayout()
        row1.setSpacing(14)
        row1.addWidget(self._build_matrix_panel(), stretch=3)
        row1.addWidget(self._build_alert_panel(), stretch=1)
        outer.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(14)
        row2.addWidget(self._build_sparkline_panel(), stretch=1)
        outer.addLayout(row2)

    def _stat_card(self, label_text, sub_text, accent_color):
        card = QFrame()
        card.setObjectName("card")
        card.setStyleSheet(
            f"QFrame#card {{ background:{SURFACE}; border:1px solid {BORDER}; "
            f"border-radius:8px; border-bottom: 3px solid {accent_color}; }}"
        )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(4)

        label = QLabel(label_text)
        label.setStyleSheet(
            f"color:{MUTED}; font-size:10px; font-weight:600; "
            f"text-transform:uppercase; letter-spacing:0.6px; border:none;"
        )
        value = QLabel("-")
        value.setStyleSheet(
            f"color:{accent_color}; font-size:26px; font-weight:700; "
            f"font-family:Consolas,monospace; border:none;"
        )
        sub = QLabel(sub_text)
        sub.setStyleSheet(f"color:{MUTED}; font-size:10px; border:none;")

        layout.addWidget(label)
        layout.addWidget(value)
        layout.addWidget(sub)

        card.value_label = value
        card.sub_label = sub
        return card

    def _build_stat_row(self):
        row = QHBoxLayout()
        row.setSpacing(12)
        self.card_high = self._stat_card("High Risk", "users flagged", RED)
        self.card_medium = self._stat_card("Medium Risk", "users to watch", YELLOW)
        self.card_low = self._stat_card("Clean Users", "no anomalies", GREEN)
        self.card_top = self._stat_card("Top Risk Score", "-", ACCENT)
        for c in (self.card_high, self.card_medium, self.card_low, self.card_top):
            row.addWidget(c)
        return row

    def _build_matrix_panel(self):
        panel, layout, meta = _panel("User Risk Matrix", ACCENT)
        self.matrix_meta = meta

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["User", "IF", "OC-SVM", "Risk Score", "Level", "Avg Files", "Transfer", "Off-Hrs"]
        )
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, 8):
            self.table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(4, 92)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setShowGrid(False)
        self.table.setStyleSheet(
            f"QTableWidget {{ border: none; }} "
            f"QTableWidget::item {{ border-bottom: 1px solid {BORDER}; }}"
        )
        layout.addWidget(self.table)
        return panel

    def _build_alert_panel(self):
        panel, layout, meta = _panel("Alert Feed", RED, "0 alerts")
        self.alert_meta = meta

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        self.alert_container = QWidget()
        self.alert_layout = QVBoxLayout(self.alert_container)
        self.alert_layout.setContentsMargins(0, 0, 0, 0)
        self.alert_layout.setSpacing(0)
        self.alert_layout.addStretch()
        scroll.setWidget(self.alert_container)
        scroll.setMinimumHeight(340)
        layout.addWidget(scroll)
        return panel

    def _build_sparkline_panel(self):
        panel, layout, _ = _panel("Risk History (last 20 ticks)", GREEN, "auto-updates with simulation")

        self.sparkline_container = QWidget()
        self.sparkline_layout = QVBoxLayout(self.sparkline_container)
        self.sparkline_layout.setContentsMargins(16, 12, 16, 14)
        self.sparkline_layout.setSpacing(10)
        layout.addWidget(self.sparkline_container)

        # Rows are added lazily in _ensure_sparkline_row() once we know which
        # users the active sector/roster actually returns.
        return panel

    def _ensure_sparkline_row(self, user):
        if user not in self._sparkline_rows:
            row_widget, bars_layout, val_label = self._make_sparkline_row(user)
            self.sparkline_layout.addWidget(row_widget)
            self._sparkline_rows[user] = (bars_layout, val_label)
        return self._sparkline_rows[user]

    def _make_sparkline_row(self, user):
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)

        label = QLabel(user)
        label.setFixedWidth(65)
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        label.setStyleSheet(f"color:{MUTED2}; font-size:11px;")

        bars_holder = QWidget()
        bars_holder.setFixedHeight(32)
        bars_layout = QHBoxLayout(bars_holder)
        bars_layout.setContentsMargins(0, 0, 0, 0)
        bars_layout.setSpacing(2)
        bars_layout.addStretch()

        val_label = QLabel("-")
        val_label.setFixedWidth(40)
        val_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        val_label.setStyleSheet(f"color:{TEXT}; font-size:11px; font-family:Consolas,monospace;")

        h.addWidget(label)
        h.addWidget(bars_holder, stretch=1)
        h.addWidget(val_label)
        return row, bars_layout, val_label

    # -- Data refresh -------------------------------------------------------
    def refresh(self):
        self._refresh_scores()
        self._refresh_alerts()

    def reset_history(self):
        self.history = {}
        while self.sparkline_layout.count():
            item = self.sparkline_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._sparkline_rows = {}

    def _refresh_scores(self):
        try:
            users = self.client.get_scores()
        except Exception:
            return

        self.last_users = users

        counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
        for u in users:
            counts[u["risk_level"]] = counts.get(u["risk_level"], 0) + 1

        top = users[0] if users else None
        self.card_high.value_label.setText(str(counts["HIGH"]))
        self.card_medium.value_label.setText(str(counts["MEDIUM"]))
        self.card_low.value_label.setText(str(counts["LOW"]))
        self.card_top.value_label.setText(f"{top['risk_score']}%" if top else "-")
        self.card_top.sub_label.setText(top["user"] if top else "-")

        self.matrix_meta.setText(f"{len(users)} users monitored")

        self.table.setRowCount(len(users))
        for row, u in enumerate(users):
            self.table.setCellWidget(row, 0, self._user_cell(u["user"], u.get("role", "")))

            for col, key in ((1, "if_score"), (2, "ocsvm_score")):
                item = QTableWidgetItem(str(u[key]))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setForeground(QColor(score_color(u[key])))
                self.table.setItem(row, col, item)

            self.table.setCellWidget(row, 3, self._score_bar_cell(u["risk_score"]))
            self.table.setCellWidget(row, 4, self._badge_cell(u["risk_level"]))

            for col, key in ((5, "avg_files"), (6, "avg_transfer"), (7, "off_hours")):
                text = f"{u[key]} MB" if key == "avg_transfer" else str(u[key])
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setForeground(QColor(MUTED2))
                self.table.setItem(row, col, item)

        self.table.resizeRowsToContents()

        for u in users:
            hist = self.history.setdefault(u["user"], [])
            hist.append(u["risk_score"])
            if len(hist) > HISTORY_MAX:
                del hist[0]
        self._update_sparklines()

    def _user_cell(self, username, role=""):
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        h = QHBoxLayout(w)
        h.setContentsMargins(10, 4, 10, 4)
        h.setSpacing(9)

        av = QLabel(username[0].upper())
        av.setFixedSize(26, 26)
        av.setAlignment(Qt.AlignmentFlag.AlignCenter)
        color = avatar_color(username)
        av.setStyleSheet(
            f"background:{color}; color:white; border-radius:13px; "
            f"font-weight:700; font-size:11px; font-family:Consolas,monospace;"
        )

        text_col = QVBoxLayout()
        text_col.setSpacing(0)
        name = QLabel(username)
        name.setStyleSheet(f"color:{TEXT}; font-weight:500; font-size:12px;")
        role_label = QLabel(str(role).title())
        role_label.setStyleSheet(f"color:{MUTED}; font-size:10px;")
        text_col.addWidget(name)
        text_col.addWidget(role_label)

        h.addWidget(av)
        h.addLayout(text_col)
        h.addStretch()
        return w

    def _score_bar_cell(self, score):
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        h = QHBoxLayout(w)
        h.setContentsMargins(8, 4, 8, 4)
        h.setSpacing(8)

        color = score_color(score)
        track = QFrame()
        track.setFixedSize(70, 4)
        track.setStyleSheet(f"background:{BORDER2}; border-radius:2px;")
        track_layout = QHBoxLayout(track)
        track_layout.setContentsMargins(0, 0, 0, 0)
        track_layout.setSpacing(0)
        fill_width = max(2, min(70, round(70 * score / 100)))
        fill = QFrame()
        fill.setFixedSize(fill_width, 4)
        fill.setStyleSheet(f"background:{color}; border-radius:2px;")
        track_layout.addWidget(fill)
        track_layout.addStretch()

        num = QLabel(f"{score}%")
        num.setStyleSheet(f"color:{color}; font-weight:600; font-size:12px; font-family:Consolas,monospace;")

        h.addWidget(track)
        h.addWidget(num)
        h.addStretch()
        return w

    def _badge_cell(self, level):
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        h = QHBoxLayout(w)
        h.setContentsMargins(8, 4, 8, 4)
        color = RISK_COLORS.get(level, TEXT)
        badge = QLabel(level)
        badge.setStyleSheet(
            f"color:{color}; border:1px solid {color}; border-radius:4px; "
            f"padding:2px 8px; font-size:10px; font-weight:700; letter-spacing:0.3px;"
        )
        h.addWidget(badge)
        h.addStretch()
        return w

    def _update_sparklines(self):
        for user, hist in self.history.items():
            bars_layout, val_label = self._ensure_sparkline_row(user)

            while bars_layout.count():
                item = bars_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

            max_v = max(hist + [1])
            for v in hist:
                h = round((v / max_v) * 28) + 2
                bar = QFrame()
                bar.setFixedHeight(h)
                bar.setMinimumWidth(3)
                bar.setSizePolicy(bar.sizePolicy().Policy.Expanding, bar.sizePolicy().Policy.Fixed)
                bar.setStyleSheet(f"background:{score_color(v)}; border-radius:2px;")
                bars_layout.addWidget(bar, alignment=Qt.AlignmentFlag.AlignBottom)
            bars_layout.addStretch()

            val_label.setText(f"{hist[-1]}%" if hist else "-")

    def _refresh_alerts(self):
        try:
            alerts = self.client.get_alerts()
        except Exception:
            return

        while self.alert_layout.count():
            item = self.alert_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.alert_meta.setText(f"{len(alerts)} alerts")

        if not alerts:
            empty = QLabel("No alerts")
            empty.setStyleSheet(f"color:{MUTED}; font-size:12px; padding:24px;")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.alert_layout.addWidget(empty)
        else:
            for a in alerts:
                self.alert_layout.addWidget(self._alert_item(a))

        self.alert_layout.addStretch()

    def _alert_item(self, a):
        color = SEVERITY_COLORS.get(a["severity"], YELLOW)

        row = QWidget()
        row.setStyleSheet(f"border-bottom: 1px solid {BORDER};")
        h = QHBoxLayout(row)
        h.setContentsMargins(14, 10, 14, 10)
        h.setSpacing(10)

        line = QFrame()
        line.setFixedWidth(2)
        line.setMinimumHeight(36)
        line.setStyleSheet(f"background:{color}; border-radius:1px;")
        h.addWidget(line)

        body = QVBoxLayout()
        body.setSpacing(2)
        user_label = QLabel(
            f"<b>{a['user']}</b> "
            f"<span style='color:{MUTED}; font-size:10px;'>login@{a['login_hour']}:00</span>"
        )
        user_label.setStyleSheet(f"color:{TEXT}; font-size:12px;")
        desc = QLabel(a["description"])
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color:{MUTED}; font-size:11px;")
        meta = QLabel(f"flagged by <b>{a['flagged_by']}</b> \u00b7 score {a['final_score']}")
        meta.setStyleSheet(f"color:{MUTED}; font-size:10px;")
        body.addWidget(user_label)
        body.addWidget(desc)
        body.addWidget(meta)
        h.addLayout(body, stretch=1)

        badge = QLabel(a["severity"])
        badge.setStyleSheet(
            f"color:{color}; background:rgba(239,68,68,30); border:1px solid {color}; border-radius:4px; "
            f"padding:2px 8px; font-size:10px; font-weight:700;"
        )
        h.addWidget(badge, alignment=Qt.AlignmentFlag.AlignTop)

        return row

