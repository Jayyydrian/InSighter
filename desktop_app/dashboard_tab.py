"""
Overview dashboard -- a 1:1 port of the web POC's dashboard.html "Overview"
section: stat row, live User Risk Matrix, Alert Feed, Risk History
sparklines, and Runtime Resources panel.
"""

import logging
import math
import time

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QTableWidget, QTableWidgetItem, QHeaderView, QScrollArea, QPushButton
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6 import sip
import zlib

from desktop_app.async_utils import run_async
from desktop_app.safe_text import plain_label

logger = logging.getLogger(__name__)

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
DEFAULT_THRESHOLDS = (45.0, 30.0)


def avatar_color(username):
    # Stable across process restarts (unlike builtin hash(), which is salted).
    return AVATAR_PALETTE[zlib.crc32(username.encode()) % len(AVATAR_PALETTE)]


def risk_level_for(score, threshold_high, threshold_medium):
    if score >= threshold_high:
        return "HIGH"
    if score >= threshold_medium:
        return "MEDIUM"
    return "LOW"


def score_color(pct, threshold_high=45, threshold_medium=30):
    return RISK_COLORS[risk_level_for(pct, threshold_high, threshold_medium)]


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

    meta = plain_label(meta_text)
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
        self.threshold_high, self.threshold_medium = DEFAULT_THRESHOLDS
        self._thresholds_fetched_at = 0.0
        self._sparkline_rows = {}
        self._sparkline_widgets = {}
        self._ai_results = {}
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

        label = plain_label(label_text)
        label.setStyleSheet(
            f"color:{MUTED}; font-size:10px; font-weight:600; "
            f"text-transform:uppercase; letter-spacing:0.6px; border:none;"
        )
        value = QLabel("-")
        value.setStyleSheet(
            f"color:{accent_color}; font-size:26px; font-weight:700; "
            f"font-family:Consolas,monospace; border:none;"
        )
        sub = plain_label(sub_text)
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

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        scroll.setMinimumHeight(160)
        scroll.setMaximumHeight(260)

        self.sparkline_container = QWidget()
        self.sparkline_layout = QVBoxLayout(self.sparkline_container)
        self.sparkline_layout.setContentsMargins(16, 12, 16, 14)
        self.sparkline_layout.setSpacing(10)
        scroll.setWidget(self.sparkline_container)
        layout.addWidget(scroll)

        # Rows are added lazily in _ensure_sparkline_row() once we know which
        # users the active sector/roster actually returns.
        return panel

    def _ensure_sparkline_row(self, user):
        if user not in self._sparkline_rows:
            row_widget, bars_layout, val_label = self._make_sparkline_row(user)
            self.sparkline_layout.addWidget(row_widget)
            self._sparkline_rows[user] = (bars_layout, val_label)
            self._sparkline_widgets[user] = row_widget
        return self._sparkline_rows[user]

    def _make_sparkline_row(self, user):
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)

        label = plain_label(user)
        label.setFixedWidth(120)
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        label.setStyleSheet(f"color:{MUTED2}; font-size:11px;")

        bars_holder = QWidget()
        bars_holder.setFixedHeight(32)
        bars_layout = QHBoxLayout(bars_holder)
        bars_layout.setContentsMargins(0, 0, 0, 0)
        bars_layout.setSpacing(2)
        bars_layout.addStretch()

        val_label = plain_label("-")
        val_label.setFixedWidth(40)
        val_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        val_label.setStyleSheet(f"color:{TEXT}; font-size:11px; font-family:Consolas,monospace;")

        h.addWidget(label)
        h.addWidget(bars_holder, stretch=1)
        h.addWidget(val_label)
        return row, bars_layout, val_label

    # -- Data refresh -------------------------------------------------------
    def refresh(self):
        fetch_thresholds = time.monotonic() - self._thresholds_fetched_at >= 30

        def fetch():
            results = {}
            requests = [
                ("scores", self.client.get_scores),
                ("alerts", self.client.get_alerts),
                ("resources", self.client.get_resources),
            ]
            if fetch_thresholds:
                requests.append(("thresholds", self.client.get_uav_config))
            for name, method in requests:
                try:
                    results[name] = method()
                except Exception as exc:
                    results[name] = exc
            return results

        run_async(
            fetch,
            self._apply_refresh,
            parent=self,
            key="refresh",
            monitor_client=self.client,
            report_success=False,
        )

    def refresh_thresholds(self):
        run_async(
            self.client.get_uav_config,
            self._apply_thresholds,
            parent=self,
            key="thresholds",
        )

    def _apply_refresh(self, results):
        for name, method in (
            ("scores", self._refresh_scores),
            ("alerts", self._refresh_alerts),
            ("resources", self._refresh_runtime_status),
        ):
            result = results[name]
            if isinstance(result, Exception):
                continue
            method(result)
        if "thresholds" in results:
            config = results["thresholds"]
            if not isinstance(config, Exception):
                self._apply_thresholds(config)
        monitor = getattr(self.client, "connection_monitor", None)
        failures = [value for value in results.values() if isinstance(value, Exception)]
        if monitor is not None:
            if failures:
                monitor.record_failure(failures[0], True, "dashboard")
            else:
                monitor.record_success("dashboard")
        else:
            for failure in failures:
                logger.warning("Failed to refresh dashboard: %s", failure)

    def _apply_thresholds(self, config):
        try:
            high = float(config["threshold_high"])
            medium = float(config["threshold_medium"])
            if not (
                math.isfinite(high)
                and math.isfinite(medium)
                and 0 <= medium < high <= 100
            ):
                raise ValueError("invalid threshold range")
        except (KeyError, TypeError, ValueError):
            logger.warning("Ignoring invalid dashboard risk thresholds.")
            return
        self.threshold_high = high
        self.threshold_medium = medium
        self._thresholds_fetched_at = time.monotonic()
        self._refresh_score_colors()

    def set_thresholds(self, high, medium):
        self._apply_thresholds({
            "threshold_high": high,
            "threshold_medium": medium,
        })

    def _refresh_score_colors(self):
        for row, user in enumerate(self.last_users):
            level = risk_level_for(
                user["risk_score"], self.threshold_high, self.threshold_medium
            )
            self.table.setCellWidget(row, 3, self._score_bar_cell(user["risk_score"]))
            self.table.setCellWidget(row, 4, self._badge_cell(level))
        if self.last_users:
            top_color = score_color(
                self.last_users[0]["risk_score"],
                self.threshold_high,
                self.threshold_medium,
            )
            self.card_top.value_label.setStyleSheet(
                f"color:{top_color}; font-size:26px; font-weight:700; "
                "font-family:Consolas,monospace; border:none;"
            )
        self._update_sparklines()

    def _refresh_runtime_status(self, resources):
        self.alert_meta.setText(
            f"{self.alert_meta.property('alert_count') or 0} alerts · "
            f"{resources.get('events_total', 0)} live events"
        )

    def reset_history(self):
        self.history = {}
        while self.sparkline_layout.count():
            item = self.sparkline_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._sparkline_rows = {}
        self._sparkline_widgets = {}

    def _refresh_scores(self, users):
        self.last_users = users
        active_users = {user["user"] for user in users}
        for stale_user in set(self.history) - active_users:
            self.history.pop(stale_user, None)
            self._sparkline_rows.pop(stale_user, None)
            widget = self._sparkline_widgets.pop(stale_user, None)
            if widget:
                self.sparkline_layout.removeWidget(widget)
                widget.deleteLater()

        counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
        for u in users:
            counts[u["risk_level"]] += 1

        top = users[0] if users else None
        self.card_high.value_label.setText(str(counts["HIGH"]))
        self.card_medium.value_label.setText(str(counts["MEDIUM"]))
        self.card_low.value_label.setText(str(counts["LOW"]))
        self.card_top.value_label.setText(f"{top['risk_score']}%" if top else "-")
        top_color = (
            score_color(top["risk_score"], self.threshold_high, self.threshold_medium)
            if top else ACCENT
        )
        self.card_top.value_label.setStyleSheet(
            f"color:{top_color}; font-size:26px; font-weight:700; "
            "font-family:Consolas,monospace; border:none;"
        )
        self.card_top.sub_label.setText(top["user"] if top else "-")

        self.matrix_meta.setText(f"{len(users)} users monitored")

        self.table.setRowCount(len(users))
        for row, u in enumerate(users):
            self.table.setCellWidget(row, 0, self._user_cell(u["user"], u.get("role", "")))

            for col, key in ((1, "if_score"), (2, "ocsvm_score")):
                item = QTableWidgetItem(str(u[key]))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setForeground(QColor(MUTED2))
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

        av = plain_label(username[0].upper())
        av.setFixedSize(26, 26)
        av.setAlignment(Qt.AlignmentFlag.AlignCenter)
        color = avatar_color(username)
        av.setStyleSheet(
            f"background:{color}; color:white; border-radius:13px; "
            f"font-weight:700; font-size:11px; font-family:Consolas,monospace;"
        )

        text_col = QVBoxLayout()
        text_col.setSpacing(0)
        name = plain_label(username)
        name.setStyleSheet(f"color:{TEXT}; font-weight:500; font-size:12px;")
        role_label = plain_label(str(role).title())
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

        color = score_color(score, self.threshold_high, self.threshold_medium)
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

        num = plain_label(f"{score}%")
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
        badge = plain_label(level)
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
                bar.setStyleSheet(
                    f"background:{score_color(v, self.threshold_high, self.threshold_medium)}; "
                    "border-radius:2px;"
                )
                bars_layout.addWidget(bar, alignment=Qt.AlignmentFlag.AlignBottom)
            bars_layout.addStretch()

            val_label.setText(f"{hist[-1]}%" if hist else "-")
            color = (
                score_color(hist[-1], self.threshold_high, self.threshold_medium)
                if hist else TEXT
            )
            val_label.setStyleSheet(
                f"color:{color}; font-size:11px; font-family:Consolas,monospace;"
            )

    def _refresh_alerts(self, alerts):
        while self.alert_layout.count():
            item = self.alert_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.alert_meta.setProperty("alert_count", len(alerts))
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
        user_label = plain_label(a["user"])
        user_label.setStyleSheet(f"color:{TEXT}; font-weight:700;")
        login_label = plain_label(f"  ·  login@{a['login_hour']}:00")
        login_label.setStyleSheet(f"color:{MUTED}; font-size:10px;")
        desc = plain_label(a["description"])
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color:{MUTED}; font-size:11px;")
        meta = plain_label(f"flagged by {a['flagged_by']} \u00b7 score {a['final_score']}")
        meta.setStyleSheet(f"color:{MUTED}; font-size:10px;")
        user_row = QHBoxLayout()
        user_row.setContentsMargins(0, 0, 0, 0)
        user_row.setSpacing(0)
        user_row.addWidget(user_label)
        user_row.addWidget(login_label)
        user_row.addStretch()
        body.addLayout(user_row)
        body.addWidget(desc)
        body.addWidget(meta)
        ai_button = QPushButton("AI Analysis")
        ai_button.setObjectName("secondary")
        ai_button.setFixedWidth(110)
        ai_panel = plain_label("")
        ai_panel.setWordWrap(True)
        ai_panel.setStyleSheet(f"color:{MUTED}; font-size:10px;")
        ai_panel.setVisible(False)
        ai_button.clicked.connect(
            lambda: self._request_ai_analysis(a["id"], ai_button, ai_panel)
        )
        body.addWidget(ai_button)
        body.addWidget(ai_panel)
        self._render_ai_state(a["id"], ai_button, ai_panel)
        h.addLayout(body, stretch=1)

        badge = plain_label(a["severity"])
        badge.setStyleSheet(
            f"color:{color}; background:rgba(239,68,68,30); border:1px solid {color}; border-radius:4px; "
            f"padding:2px 8px; font-size:10px; font-weight:700;"
        )
        h.addWidget(badge, alignment=Qt.AlignmentFlag.AlignTop)

        return row

    def _request_ai_analysis(self, alert_id, button, panel):
        if self._ai_results.get(alert_id, {}).get("status") == "loading":
            return
        self._ai_results[alert_id] = {"status": "loading"}
        self._render_ai_state(alert_id, button, panel)
        run_async(
            lambda: self.client.explain_alert(alert_id),
            lambda result: self._show_ai_result(alert_id, result, button, panel),
            lambda exc: self._show_ai_result(
                alert_id,
                {
                    "available": False,
                    "reason": str(exc) or "AI analysis unavailable",
                    "explanation": None,
                    "recommendation": None,
                },
                button,
                panel,
            ),
            parent=self,
            monitor_client=self.client,
            monitor_source="dashboard-ai",
            counts_toward_offline=False,
            report_success=False,
        )

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
