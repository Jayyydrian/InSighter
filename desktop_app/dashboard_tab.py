"""Overview dashboard with risk summary, user matrix, and alert feed."""

import hashlib
import json
import logging
import math
import time
from types import SimpleNamespace

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QBoxLayout, QLabel, QFrame,
    QTableWidget, QTableWidgetItem, QHeaderView, QScrollArea, QPushButton,
    QToolButton, QMenu, QComboBox, QSizePolicy, QCheckBox,
)
from PyQt6.QtCore import (
    Qt, QSettings, pyqtSignal,
)
from PyQt6.QtGui import (
    QAction, QColor, QFontMetrics, QPainter, QPen,
)
from PyQt6 import sip
import zlib

from desktop_app.async_utils import run_async
from desktop_app.risk import risk_color_for_level
from desktop_app.safe_text import plain_label
from desktop_app.theme import (
    BORDER, BORDER2, MUTED, MUTED2, TEXT, InsetShadowFrame,
)
from risk_thresholds import risk_level_for as shared_risk_level_for

logger = logging.getLogger(__name__)

RED = "#ef4444"
YELLOW = "#f59e0b"
GREEN = "#10b981"
ACCENT = "#3b82f6"

CRITICAL = "#dc2626"
RISK_COLORS = {"HIGH": RED, "MEDIUM": YELLOW, "LOW": GREEN}
SEVERITY_COLORS = {"CRITICAL": CRITICAL, "HIGH": RED, "MEDIUM": YELLOW}

# Users and roles are whatever the active sector/roster returns from the API
# (see /api/scores), not a fixed cast -- so avatar colors are derived from the
# username instead of looked up in a static per-demo-user table.
AVATAR_PALETTE = ["#3b82f6", "#8b5cf6", "#06b6d4", "#10b981", "#ef4444", "#f59e0b", "#ec4899", "#14b8a6"]

HISTORY_MAX = 20
DEFAULT_THRESHOLD_HIGH = 45
DEFAULT_THRESHOLD_MEDIUM = 30


def risk_level_for(score, threshold_high=DEFAULT_THRESHOLD_HIGH,
                  threshold_medium=DEFAULT_THRESHOLD_MEDIUM):
    return shared_risk_level_for(score, threshold_high, threshold_medium)


def avatar_color(username):
    # Stable across process restarts (unlike builtin hash(), which is salted).
    return AVATAR_PALETTE[zlib.crc32(username.encode()) % len(AVATAR_PALETTE)]


def score_color(
    pct,
    threshold_high=DEFAULT_THRESHOLD_HIGH,
    threshold_medium=DEFAULT_THRESHOLD_MEDIUM,
):
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


GRID_COLUMNS = 12
GRID_ROW_RATIO = 0.28
GRID_GAP_RATIO = 0.06
PANEL_DEFAULTS = {
    "summary": [0, 0, 12, 8],
    "matrix": [0, 8, 8, 16],
    "alerts": [8, 8, 4, 16],
}
PANEL_MINIMUMS = {
    "summary": (6, 7),
    "matrix": (6, 5),
    "alerts": (4, 5),
}
PANEL_TITLES = {
    "summary": "Risk Summary",
    "matrix": "User Risk Matrix",
    "alerts": "Alert Feed",
}


class _PanelHeader(QWidget):
    def __init__(self, workspace, panel_id, parent=None):
        super().__init__(parent)
        self.workspace = workspace
        self.panel_id = panel_id
        self.setCursor(Qt.CursorShape.SizeAllCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.workspace.begin_drag(self.panel_id, event.globalPosition().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.workspace.update_drag(event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.workspace.end_interaction()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _ResizeGrip(QToolButton):
    def __init__(self, workspace, panel_id, parent=None):
        super().__init__(parent)
        self.workspace = workspace
        self.panel_id = panel_id
        self.setText("\u25e2")
        self.setToolTip("Resize to grid")
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self.setStyleSheet(
            f"QToolButton {{ color:{MUTED}; background:transparent; border:none; padding:2px 6px; }}"
            f"QToolButton:hover {{ color:{TEXT}; }}"
        )

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.workspace.begin_resize(self.panel_id, event.globalPosition().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.workspace.update_resize(event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.workspace.end_interaction()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class DashboardPanel(InsetShadowFrame):
    resized = pyqtSignal(int, int)

    def __init__(self, workspace, panel_id, title_text, dot_color, meta_text=""):
        super().__init__(workspace)
        self.workspace = workspace
        self.panel_id = panel_id
        self.setObjectName("card")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(5, 5, 5, 5)
        outer.setSpacing(0)

        header = _PanelHeader(workspace, panel_id, self)
        head_layout = QHBoxLayout(header)
        head_layout.setContentsMargins(10, 6, 6, 8)
        dot = QLabel("\u25cf")
        dot.setStyleSheet(f"color:{dot_color}; font-size:9px;")
        dot.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        title = QLabel(title_text)
        title.setStyleSheet(f"color:{TEXT}; font-weight:600; font-size:12px;")
        title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        head_layout.addWidget(dot)
        head_layout.addWidget(title)
        head_layout.addStretch()

        self.meta = plain_label(meta_text)
        self.meta.setStyleSheet(f"color:{MUTED}; font-size:10px;")
        self.meta.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        head_layout.addWidget(self.meta)

        close = QToolButton()
        close.setText("\u00d7")
        close.setToolTip("Close panel")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(
            f"QToolButton {{ color:{MUTED}; background:transparent; border:none; font-size:16px; padding:0 5px; }}"
            f"QToolButton:hover {{ color:{TEXT}; }}"
        )
        close.clicked.connect(lambda: workspace.hide_panel(panel_id))
        head_layout.addWidget(close)
        outer.addWidget(header)

        self.content_layout = QVBoxLayout()
        self.content_layout.setContentsMargins(8, 0, 8, 2)
        self.content_layout.setSpacing(0)
        outer.addLayout(self.content_layout, stretch=1)

        footer = QHBoxLayout()
        footer.addStretch()
        footer.addWidget(_ResizeGrip(workspace, panel_id, self))
        footer.setContentsMargins(0, 0, 0, 0)
        outer.addLayout(footer)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resized.emit(event.size().width(), event.size().height())


class _SnapGrid(QWidget):
    def __init__(self, workspace):
        super().__init__(workspace)
        self.workspace = workspace
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(QPen(QColor(255, 255, 255, 20), 1))
        cell, row, _gap = self.workspace.grid_metrics()
        for column in range(GRID_COLUMNS + 1):
            x = round(column * cell)
            painter.drawLine(x, 0, x, self.height())
        for row_index in range(math.ceil(self.height() / row) + 1):
            y = round(row_index * row)
            painter.drawLine(0, y, self.width(), y)


class _ElidedLabel(QLabel):
    def __init__(self, text="", alignment=Qt.AlignmentFlag.AlignCenter, parent=None):
        super().__init__(text, parent)
        self.alignment = alignment
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setMinimumSize(0, 0)
        self.setToolTip(text)

    def setText(self, text):
        super().setText(text)
        self.setToolTip(text)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setFont(self.font())
        painter.setPen(self.palette().color(self.foregroundRole()))
        metrics = QFontMetrics(self.font())
        text = metrics.elidedText(
            self.text(), Qt.TextElideMode.ElideRight, max(0, self.width() - 4)
        )
        painter.drawText(self.rect(), self.alignment, text)


class DashboardWorkspace(QWidget):
    """Fluid 12-column workspace with collision-free drag, resize and snap."""

    visibility_changed = pyqtSignal()

    def __init__(self, settings, settings_key, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.settings_key = settings_key
        self.panels = {}
        self.positions = {key: value[:] for key, value in PANEL_DEFAULTS.items()}
        self.visible = set(PANEL_DEFAULTS)
        self._interaction = None
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.grid_layer = _SnapGrid(self)
        self.grid_layer.lower()

    def register(self, panel):
        self.panels[panel.panel_id] = panel

    def load_saved_layout(self):
        raw = self.settings.value(self.settings_key, "")
        if not raw:
            self.layout_panels()
            return
        migrated_removed_panel = False
        try:
            state = json.loads(raw)
            positions = dict(state["positions"])
            visible = set(state["visible"])
            if (
                set(positions) == (set(PANEL_DEFAULTS) | {"history"})
                and visible <= (set(PANEL_DEFAULTS) | {"history"})
            ):
                positions.pop("history")
                visible.discard("history")
                migrated_removed_panel = True
            if set(positions) != set(PANEL_DEFAULTS) or not visible <= set(PANEL_DEFAULTS):
                raise ValueError("unexpected dashboard panel set")
            validated = {}
            for panel_id, values in positions.items():
                if not isinstance(values, list) or len(values) != 4:
                    raise ValueError("invalid panel geometry")
                x, y, width, height = (int(value) for value in values)
                min_width, min_height = PANEL_MINIMUMS[panel_id]
                if (
                    x < 0 or y < 0 or width < min_width or height < min_height
                    or x + width > GRID_COLUMNS
                ):
                    raise ValueError("panel geometry is outside the grid")
                validated[panel_id] = [x, y, width, height]
            self.positions = validated
            self.visible = visible
            for panel_id in visible:
                if not self._fits(panel_id, *validated[panel_id]):
                    raise ValueError("saved panels overlap")
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
            logger.warning("Ignoring invalid saved dashboard layout: %s", exc)
            self.positions = {key: value[:] for key, value in PANEL_DEFAULTS.items()}
            self.visible = set(PANEL_DEFAULTS)

        for panel_id, panel in self.panels.items():
            panel.setVisible(panel_id in self.visible)
        self.layout_panels()
        if migrated_removed_panel:
            self.save_layout()
        self.visibility_changed.emit()

    def save_layout(self):
        state = {"positions": self.positions, "visible": sorted(self.visible)}
        self.settings.setValue(self.settings_key, json.dumps(state))
        self.settings.sync()
        if self.settings.status() != QSettings.Status.NoError:
            logger.warning("Could not persist the dashboard layout in application settings.")

    def reset_layout(self):
        self.positions = {key: value[:] for key, value in PANEL_DEFAULTS.items()}
        self.visible = set(PANEL_DEFAULTS)
        for panel in self.panels.values():
            panel.show()
        self.layout_panels()
        self.save_layout()
        self.visibility_changed.emit()

    def hide_panel(self, panel_id):
        if panel_id in self.visible:
            self.visible.remove(panel_id)
            self.panels[panel_id].hide()
            self.layout_panels()
            self.save_layout()
            self.visibility_changed.emit()

    def show_panel(self, panel_id):
        if panel_id in self.visible:
            return
        rect = self.positions[panel_id]
        if not self._fits(panel_id, *rect):
            width, height = rect[2], rect[3]
            bottom = max(
                (self.positions[key][1] + self.positions[key][3])
                for key in self.visible
            ) if self.visible else 0
            for y in range(bottom + 1):
                for x in range(GRID_COLUMNS - width + 1):
                    if self._fits(panel_id, x, y, width, height):
                        self.positions[panel_id] = [x, y, width, height]
                        break
                else:
                    continue
                break
            else:
                self.positions[panel_id] = [0, bottom, width, height]
        self.visible.add(panel_id)
        self.panels[panel_id].show()
        self.layout_panels()
        self.save_layout()
        self.visibility_changed.emit()

    def move_panel(self, panel_id, x, y, persist=True):
        _, _, width, height = self.positions[panel_id]
        return self.resize_or_move(panel_id, x, y, width, height, persist)

    def resize_or_move(self, panel_id, x, y, width, height, persist=True):
        x, y, width, height = map(int, (x, y, width, height))
        if not self._fits(panel_id, x, y, width, height):
            return False
        self.positions[panel_id] = [x, y, width, height]
        self.layout_panels()
        if persist:
            self.save_layout()
        return True

    def _fits(self, panel_id, x, y, width, height):
        min_width, min_height = PANEL_MINIMUMS[panel_id]
        if (
            x < 0 or y < 0 or width < min_width or height < min_height
            or x + width > GRID_COLUMNS
        ):
            return False
        for other_id in self.visible:
            if other_id == panel_id:
                continue
            ox, oy, ow, oh = self.positions[other_id]
            if x < ox + ow and x + width > ox and y < oy + oh and y + height > oy:
                return False
        return True

    def begin_drag(self, panel_id, global_pos):
        x, y, width, height = self.positions[panel_id]
        self._interaction = ("move", panel_id, global_pos, [x, y, width, height])

    def begin_resize(self, panel_id, global_pos):
        x, y, width, height = self.positions[panel_id]
        self._interaction = ("resize", panel_id, global_pos, [x, y, width, height])

    def update_drag(self, global_pos):
        if not self._interaction or self._interaction[0] != "move":
            return
        _, panel_id, origin, rect = self._interaction
        dx, dy = self._grid_delta(global_pos - origin)
        self.resize_or_move(panel_id, rect[0] + dx, rect[1] + dy, rect[2], rect[3], False)

    def update_resize(self, global_pos):
        if not self._interaction or self._interaction[0] != "resize":
            return
        _, panel_id, origin, rect = self._interaction
        dw, dh = self._grid_delta(global_pos - origin)
        self.resize_or_move(panel_id, rect[0], rect[1], rect[2] + dw, rect[3] + dh, False)

    def _grid_delta(self, delta):
        cell = max(1, self.width() / GRID_COLUMNS)
        row = max(1, cell * GRID_ROW_RATIO)
        return round(delta.x() / cell), round(delta.y() / row)

    def end_interaction(self):
        if self._interaction:
            self._interaction = None
            self.save_layout()

    def layout_panels(self):
        cell, row, gap = self.grid_metrics()
        bottom = 0
        self.grid_layer.setGeometry(self.rect())
        self.grid_layer.lower()
        for panel_id in self.visible:
            x, y, width, height = self.positions[panel_id]
            px = round(x * cell + gap)
            py = round(y * row + gap)
            pw = max(1, round(width * cell - 2 * gap))
            ph = max(1, round(height * row - 2 * gap))
            self.panels[panel_id].setGeometry(px, py, pw, ph)
            self.panels[panel_id].raise_()
            bottom = max(bottom, round((y + height) * row + gap))
        self.setMinimumHeight(bottom)

    def grid_metrics(self):
        cell = max(1, self.width() / GRID_COLUMNS)
        row = cell * GRID_ROW_RATIO
        gap = max(1, round(cell * GRID_GAP_RATIO))
        return cell, row, gap

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.layout_panels()
        self.grid_layer.update()

    def sizeHint(self):
        cell = max(1, self.width() / GRID_COLUMNS)
        row = cell * GRID_ROW_RATIO
        bottom = max(
            (self.positions[panel_id][1] + self.positions[panel_id][3])
            for panel_id in self.visible
        ) if self.visible else 1
        from PyQt6.QtCore import QSize
        return QSize(self.width(), round(bottom * row))


class DashboardTab(QWidget):
    """The Overview page: risk summary, trend, matrix, and alert feed."""

    def __init__(self, client, settings=None):
        super().__init__()
        self.client = client
        self.settings = settings or QSettings("InSighter", "InSighter")
        username = str(getattr(client, "username", "default"))
        user_key = hashlib.sha256(username.encode("utf-8")).hexdigest()[:16]
        self.layout_settings_key = f"dashboard-layout/{user_key}"
        self.history = {}
        self.last_users = []
        self.alerts = []
        self.threshold_high = DEFAULT_THRESHOLD_HIGH
        self.threshold_medium = DEFAULT_THRESHOLD_MEDIUM
        self._thresholds_fetched_at = 0.0
        self._ai_results = {}
        self._build_ui()

    # -- UI construction --------------------------------------------------
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        toolbar = QHBoxLayout()
        toolbar.addStretch()
        self.add_panel_button = QToolButton()
        self.add_panel_button.setText("Add panel \u25be")
        self.add_panel_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.add_panel_menu = QMenu(self.add_panel_button)
        self.add_panel_button.setMenu(self.add_panel_menu)
        toolbar.addWidget(self.add_panel_button)

        self.reset_layout_button = QPushButton("Reset to default")
        self.reset_layout_button.clicked.connect(self.reset_layout)
        toolbar.addWidget(self.reset_layout_button)
        outer.addLayout(toolbar)

        self.workspace = DashboardWorkspace(
            self.settings, self.layout_settings_key, parent=self
        )
        self.workspace.visibility_changed.connect(self._update_add_panel_menu)
        self.panels = {
            "summary": self._build_summary_panel(),
            "matrix": self._build_matrix_panel(),
            "alerts": self._build_alert_panel(),
        }
        for panel in self.panels.values():
            self.workspace.register(panel)
        self.panels["matrix"].resized.connect(self._resize_matrix_controls)
        self.workspace.load_saved_layout()
        self._update_add_panel_menu()
        outer.addWidget(self.workspace)

    def _panel(self, panel_id, dot_color, meta_text=""):
        return DashboardPanel(
            self.workspace,
            panel_id,
            PANEL_TITLES[panel_id],
            dot_color,
            meta_text,
        )

    def _update_add_panel_menu(self):
        self.add_panel_menu.clear()
        for panel_id, title in PANEL_TITLES.items():
            if panel_id in self.workspace.visible:
                continue
            action = QAction(title, self.add_panel_menu)
            action.triggered.connect(
                lambda _checked=False, key=panel_id: self._show_panel(key)
            )
            self.add_panel_menu.addAction(action)
        self.add_panel_button.setEnabled(bool(self.add_panel_menu.actions()))

    def _show_panel(self, panel_id):
        self.workspace.show_panel(panel_id)
        self._update_add_panel_menu()

    def reset_layout(self):
        self.workspace.reset_layout()
        self._update_add_panel_menu()

    def _build_summary_panel(self):
        panel = self._panel("summary", ACCENT)
        self.metric_table = QTableWidget(1, 4)
        self.metric_table.setHorizontalHeaderLabels(
            ["Total Risk %", "High Risk", "Medium Risk", "Low Risk"]
        )
        horizontal_header = self.metric_table.horizontalHeader()
        horizontal_header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        horizontal_header.setMinimumSectionSize(0)
        horizontal_header.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        horizontal_header.setToolTip("Total Risk %, High Risk, Medium Risk, Low Risk")
        vertical_header = self.metric_table.verticalHeader()
        vertical_header.setVisible(False)
        vertical_header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.metric_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.metric_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.metric_table.setShowGrid(False)
        self.metric_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.metric_table.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.metric_table.setMinimumSize(0, 0)
        self.metric_table.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.metric_table.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.metric_table.setStyleSheet(
            f"QTableWidget {{ border:none; background:transparent; }}"
            f"QHeaderView::section {{ background:transparent; border-bottom:1px solid {BORDER}; padding:6px 8px; }}"
            f"QTableWidget::item {{ border:none; }}"
        )

        metric_specs = (
            ("card_top", "top risk score", ACCENT),
            ("card_high", "users flagged", RED),
            ("card_medium", "users to watch", YELLOW),
            ("card_low", "no anomalies", GREEN),
        )
        for column, (attribute, description, color) in enumerate(metric_specs):
            cell = QWidget()
            cell.setMinimumSize(0, 0)
            cell.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
            cell_layout = QVBoxLayout(cell)
            cell_layout.setContentsMargins(4, 4, 4, 4)
            cell_layout.setSpacing(4)
            value = _ElidedLabel("Loading…")
            value.setStyleSheet(
                f"color:{color}; font-size:36px; font-weight:700; "
                "font-family:Consolas,monospace; border:none;"
            )
            sub = _ElidedLabel("Loading risk data")
            sub.setStyleSheet(f"color:{MUTED}; border:none;")
            cell_layout.addWidget(value)
            cell_layout.addWidget(sub)
            self.metric_table.setCellWidget(0, column, cell)
            setattr(self, attribute, SimpleNamespace(value_label=value, sub_label=sub))

        self.metric_table.setRowHeight(0, 1)
        for column, description in enumerate(
            ("Total Risk %", "High Risk", "Medium Risk", "Low Risk")
        ):
            self.metric_table.horizontalHeaderItem(column).setToolTip(description)
        panel.content_layout.addWidget(self.metric_table)
        return panel

    def _build_matrix_panel(self):
        panel = self._panel("matrix", ACCENT)
        self.matrix_meta = panel.meta
        filter_container = QWidget()
        filter_container.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.matrix_filter_layout = QBoxLayout(
            QBoxLayout.Direction.LeftToRight, filter_container
        )
        self.matrix_filter_layout.setContentsMargins(0, 2, 0, 6)
        self.matrix_filter_layout.setSpacing(8)
        self.matrix_filter_label = plain_label("Risk level")
        self.matrix_filter_layout.addWidget(self.matrix_filter_label)
        self.matrix_risk_filter = QComboBox()
        self.matrix_risk_filter.addItem("High", "HIGH")
        self.matrix_risk_filter.addItem("Medium", "MEDIUM")
        self.matrix_risk_filter.addItem("Low", "LOW")
        self.matrix_risk_filter.addItem("All levels", None)
        self.matrix_risk_filter.setMinimumWidth(0)
        self.matrix_risk_filter.setMaximumWidth(150)
        self.matrix_risk_filter.currentIndexChanged.connect(self._render_matrix)
        self.matrix_filter_layout.addWidget(self.matrix_risk_filter)
        panel.content_layout.addWidget(filter_container)

        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(
            ["User", "IF score", "SVM score", "Risk score", "Level",
             "Files / event", "Data / event", "Off-hours", "Trend"]
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(0, 220)
        for column in range(1, 8):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        header.setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        for column, tooltip in enumerate((
            "User and assigned role.",
            "Isolation Forest anomaly score (0-100). Higher values indicate more unusual activity.",
            "One-Class SVM anomaly score (0-100). Higher values indicate more unusual activity.",
            "Combined risk score.",
            "Server-assigned risk level.",
            "Average files accessed per recorded activity event.",
            "Average data transferred per recorded activity event, in megabytes (MB).",
            "Total recorded activity events outside usual hours.",
            "Recent risk scores, colored by server-assigned risk level.",
        )):
            header_item = self.table.horizontalHeaderItem(column)
            header_item.setToolTip(tooltip)
            if column:
                header_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setMinimumSize(0, 0)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.setStyleSheet(
            f"QTableWidget {{ border:none; alternate-background-color:{BORDER}; }}"
            f"QTableWidget::item {{ border-bottom:1px solid {BORDER}; padding:4px 6px; }}"
            f"QHeaderView::section {{ background:#111111; color:{MUTED}; "
            f"border:none; border-bottom:1px solid {BORDER}; padding:7px 6px; "
            "font-size:10px; font-weight:700; }"
        )
        panel.content_layout.addWidget(self.table)
        return panel

    def _resize_matrix_controls(self, panel_width, _panel_height):
        if not hasattr(self, "matrix_filter_layout"):
            return
        available_width = max(0, panel_width - 40)
        if available_width < 340:
            self.matrix_filter_layout.setDirection(
                QBoxLayout.Direction.TopToBottom
            )
            self.matrix_risk_filter.setMaximumWidth(
                max(0, available_width - 8)
            )
        else:
            self.matrix_filter_layout.setDirection(
                QBoxLayout.Direction.LeftToRight
            )
            self.matrix_risk_filter.setMaximumWidth(150)

    def _build_alert_panel(self):
        panel = self._panel("alerts", RED, "0 alerts")
        self.alert_meta = panel.meta
        self.alert_filter_layout = QBoxLayout(
            QBoxLayout.Direction.LeftToRight
        )
        self.alert_filter_layout.setContentsMargins(0, 2, 0, 6)
        self.alert_filter_layout.setSpacing(8)
        self.alert_filter_layout.addWidget(plain_label("Severity"))
        self.alert_severity_filter = QComboBox()
        self.alert_severity_filter.addItem("Critical", "CRITICAL")
        self.alert_severity_filter.addItem("High", "HIGH")
        self.alert_severity_filter.addItem("Medium", "MEDIUM")
        self.alert_severity_filter.addItem("All alerts", None)
        self.alert_severity_filter.setMinimumWidth(0)
        self.alert_severity_filter.setMaximumWidth(150)
        self.alert_severity_filter.currentIndexChanged.connect(
            self._render_alerts
        )
        self.alert_filter_layout.addWidget(self.alert_severity_filter)
        panel.content_layout.addLayout(self.alert_filter_layout)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        scroll.setMinimumHeight(0)
        self.alert_container = QWidget()
        self.alert_layout = QVBoxLayout(self.alert_container)
        self.alert_layout.setContentsMargins(0, 0, 0, 0)
        self.alert_layout.setSpacing(0)
        self.alert_layout.addStretch()
        scroll.setWidget(self.alert_container)
        panel.content_layout.addWidget(scroll)
        self.alerts = []
        return panel

    # -- Data refresh -------------------------------------------------------
    def refresh(self):
        def fetch():
            requests = [
                ("scores", self.client.get_scores),
                ("alerts", self.client.get_alerts),
                ("resources", self.client.get_resources),
                ("thresholds", self.client.get_uav_config),
            ]
            results = {}
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

    def _show_summary_status(self, value, detail):
        for card in (
            self.card_top,
            self.card_high,
            self.card_medium,
            self.card_low,
        ):
            card.value_label.setText(value)
            card.sub_label.setText(detail)

    def _apply_refresh(self, results):
        for name, method in (
            ("scores", self._refresh_scores),
            ("alerts", self._refresh_alerts),
            ("resources", self._refresh_runtime_status),
            ("thresholds", self._apply_thresholds),
        ):
            result = results[name]
            if isinstance(result, Exception):
                if name == "scores":
                    self._show_summary_status("Unavailable", "Could not load risk data")
                continue
            method(result)
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
        self._render_matrix()
        if self.last_users:
            top = max(
                self.last_users,
                key=lambda user: float(user.get("risk_score", 0)),
            )
            top_color = score_color(
                top["risk_score"], self.threshold_high, self.threshold_medium
            )
            self.card_top.sub_label.setStyleSheet(f"color:{top_color}; border:none;")

    def _refresh_runtime_status(self, resources):
        self.alert_meta.setText(
            f"{self.alert_meta.property('alert_count') or 0} alerts · "
            f"{resources.get('events_total', 0)} live events"
        )

    def reset_history(self):
        self.history = {}

    def _refresh_scores(self, users):
        normalized_users = []
        for user in users:
            if not isinstance(user, dict):
                continue
            employee = user.get("user") or user.get("id")
            if not employee:
                continue
            normalized = dict(user)
            normalized["user"] = str(employee)
            normalized["role"] = str(
                user.get("department") or user.get("role") or "Unknown"
            )
            for key in (
                "if_score", "ocsvm_score", "avg_files", "avg_transfer", "off_hours"
            ):
                normalized.setdefault(key, 0)
            if not isinstance(normalized.get("risk_score"), (int, float)):
                normalized["risk_score"] = 0.0
            if normalized.get("risk_level") not in RISK_COLORS:
                normalized["risk_level"] = risk_level_for(
                    normalized["risk_score"],
                    self.threshold_high,
                    self.threshold_medium,
                )
            normalized_users.append(normalized)
        self.last_users = normalized_users
        active_users = {user["user"] for user in normalized_users}
        for stale_user in set(self.history) - active_users:
            self.history.pop(stale_user, None)

        counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
        for user in normalized_users:
            level = user.get("risk_level")
            if level in counts:
                counts[level] += 1

        top = max(
            normalized_users,
            key=lambda user: float(user["risk_score"]),
            default=None,
        )
        self.card_top.value_label.setText(f"{top['risk_score']}%" if top else "0%")
        self.card_top.sub_label.setText(
            f"Top risk score · {top['user']}" if top else "No users monitored"
        )
        self.card_top.sub_label.setStyleSheet(
            f"color:{risk_color_for_level(top['risk_level']) if top else MUTED}; "
            "border:none;"
        )
        for level, card in (
            ("HIGH", self.card_high),
            ("MEDIUM", self.card_medium),
            ("LOW", self.card_low),
        ):
            card.value_label.setText(str(counts[level]))
        self.matrix_meta.setText(f"{len(normalized_users)} users monitored")

        self._render_matrix()
        for user in normalized_users:
            history = self.history.setdefault(user["user"], [])
            history.append((float(user["risk_score"]), user["risk_level"]))
            if len(history) > HISTORY_MAX:
                del history[0]
        self._update_sparklines()

    def _render_matrix(self, *_args):
        if not hasattr(self, "matrix_risk_filter") or not hasattr(self, "table"):
            return
        selected_level = self.matrix_risk_filter.currentData()
        users = [
            user for user in self.last_users
            if selected_level is None or user.get("risk_level") == selected_level
        ]
        self._sparkline_rows = {}
        self.matrix_meta.setText(f"Showing {len(users)} of {len(self.last_users)} users")
        self.table.setRowCount(len(users))
        for row, user in enumerate(users):
            self.table.setCellWidget(
                row, 0, self._user_cell(user["user"], user.get("role", ""))
            )
            for column, key in ((1, "if_score"), (2, "ocsvm_score")):
                item = QTableWidgetItem(self._format_matrix_value(user.get(key)))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setForeground(QColor(MUTED2))
                self.table.setItem(row, column, item)
            self.table.setCellWidget(
                row, 3, self._score_bar_cell(
                    float(user["risk_score"]), user["risk_level"]
                )
            )
            self.table.setCellWidget(row, 4, self._badge_cell(user["risk_level"]))
            for column, key in (
                (5, "avg_files"), (6, "avg_transfer"), (7, "off_hours")
            ):
                suffix = " MB" if key == "avg_transfer" else ""
                precision = 0 if key == "off_hours" else 1
                item = QTableWidgetItem(
                    self._format_matrix_value(user.get(key), suffix, precision)
                )
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setForeground(QColor(MUTED2))
                self.table.setItem(row, column, item)
            self.table.setCellWidget(row, 8, self._sparkline_cell(user["user"]))
        for row in range(len(users)):
            self.table.setRowHeight(row, 42)
        self._update_sparklines()

    def _sparkline_cell(self, username):
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        bars_holder = QWidget()
        bars_holder.setFixedHeight(28)
        bars_layout = QHBoxLayout(bars_holder)
        bars_layout.setContentsMargins(0, 0, 0, 0)
        bars_layout.setSpacing(2)
        value_label = plain_label("-")
        value_label.setFixedWidth(42)
        value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        value_label.setStyleSheet(
            f"color:{TEXT}; font-size:10px; font-family:Consolas,monospace;"
        )
        layout.addWidget(bars_holder, stretch=1)
        layout.addWidget(value_label)
        self._sparkline_rows[username] = (bars_layout, value_label)
        return widget

    def _update_sparklines(self):
        for username, (bars_layout, value_label) in self._sparkline_rows.items():
            while bars_layout.count():
                item = bars_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

            history = self.history.get(username, [])
            max_score = max((score for score, _level in history), default=1) or 1
            for score, level in history:
                bar = QFrame()
                bar.setFixedHeight(max(2, round(score / max_score * 24)))
                bar.setMinimumWidth(3)
                bar.setSizePolicy(
                    QSizePolicy.Policy.Expanding,
                    QSizePolicy.Policy.Fixed,
                )
                bar.setStyleSheet(
                    f"background:{risk_color_for_level(level)}; border-radius:2px;"
                )
                bars_layout.addWidget(
                    bar, alignment=Qt.AlignmentFlag.AlignBottom
                )
            bars_layout.addStretch()

            if history:
                score, level = history[-1]
                value_label.setText(f"{score:.1f}%")
                color = risk_color_for_level(level)
            else:
                value_label.setText("-")
                color = TEXT
            value_label.setStyleSheet(
                f"color:{color}; font-size:10px; font-family:Consolas,monospace;"
            )

    @staticmethod
    def _format_matrix_value(value, suffix="", precision=1):
        if not isinstance(value, (int, float)):
            return "-"
        return f"{float(value):.{precision}f}{suffix}"

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

    def _score_bar_cell(self, score, risk_level):
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        h = QHBoxLayout(w)
        h.setContentsMargins(8, 4, 8, 4)
        h.setSpacing(8)

        color = risk_color_for_level(risk_level)
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

        num = plain_label(f"{score:.1f}%")
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
        color = risk_color_for_level(level)
        badge = plain_label(level)
        badge.setStyleSheet(
            f"color:{color}; border:1px solid {color}; border-radius:4px; "
            f"padding:2px 8px; font-size:10px; font-weight:700; letter-spacing:0.3px;"
        )
        h.addWidget(badge)
        h.addStretch()
        return w

    def _refresh_alerts(self, alerts):
        self.alerts = list(alerts)
        self._render_alerts()

    def _render_alerts(self, *_args):
        while self.alert_layout.count():
            item = self.alert_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        selected_severity = self.alert_severity_filter.currentData()
        alerts = [
            alert for alert in self.alerts
            if selected_severity is None
            or str(alert.get("severity", "")).upper() == selected_severity
        ]
        self.alert_meta.setProperty("alert_count", len(alerts))
        total = len(self.alerts)
        self.alert_meta.setText(
            f"{len(alerts)} of {total} alerts"
            if len(alerts) != total else f"{total} alerts"
        )

        if not alerts:
            empty = QLabel("No alerts")
            empty.setStyleSheet(f"color:{MUTED}; font-size:12px; padding:24px;")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.alert_layout.addWidget(empty)
        else:
            for alert in alerts:
                self.alert_layout.addWidget(self._alert_item(alert))

        self.alert_layout.addStretch()

    def _alert_item(self, alert):
        color = SEVERITY_COLORS.get(alert["severity"], YELLOW)
        row = QWidget()
        row.setStyleSheet(f"border-bottom: 1px solid {BORDER};")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(10)

        line = QFrame()
        line.setFixedWidth(2)
        line.setMinimumHeight(36)
        line.setStyleSheet(f"background:{color}; border-radius:1px;")
        layout.addWidget(line)

        body = QVBoxLayout()
        body.setSpacing(2)
        user_label = plain_label(alert["user"])
        user_label.setStyleSheet(f"color:{TEXT}; font-weight:700;")
        login_label = plain_label(f"  ·  login@{alert['login_hour']}:00")
        login_label.setStyleSheet(f"color:{MUTED}; font-size:10px;")
        description = plain_label(alert["description"])
        description.setWordWrap(True)
        description.setStyleSheet(f"color:{MUTED}; font-size:11px;")
        meta = plain_label(
            f"flagged by {alert['flagged_by']} · score {alert['final_score']}"
        )
        meta.setStyleSheet(f"color:{MUTED}; font-size:10px;")
        user_row = QHBoxLayout()
        user_row.setContentsMargins(0, 0, 0, 0)
        user_row.setSpacing(0)
        user_row.addWidget(user_label)
        user_row.addWidget(login_label)
        user_row.addStretch()
        body.addLayout(user_row)
        body.addWidget(description)
        body.addWidget(meta)
        ai_button = QPushButton("AI Analysis")
        ai_button.setObjectName("secondary")
        ai_button.setFixedWidth(110)
        ai_panel = plain_label("")
        ai_panel.setWordWrap(True)
        ai_panel.setStyleSheet(f"color:{MUTED}; font-size:10px;")
        ai_panel.setVisible(False)
        ai_button.clicked.connect(
            lambda: self._request_ai_analysis(alert["id"], ai_button, ai_panel)
        )
        body.addWidget(ai_button)
        body.addWidget(ai_panel)
        self._render_ai_state(alert["id"], ai_button, ai_panel)
        layout.addLayout(body, stretch=1)

        badge = plain_label(alert["severity"])
        badge.setStyleSheet(
            f"color:{color}; background:rgba(239,68,68,30); border:1px solid {color}; "
            "border-radius:4px; padding:2px 8px; font-size:10px; font-weight:700;"
        )
        layout.addWidget(badge, alignment=Qt.AlignmentFlag.AlignTop)
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
        button.setText(
            "Analyzing..." if entry["status"] == "loading"
            else "Refresh AI Analysis"
        )
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
