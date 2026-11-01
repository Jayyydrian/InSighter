from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QFrame, QWidget


BG = "#080808"
SURFACE = "#101010"
SURFACE2 = "#191919"
BORDER = "#292929"
BORDER2 = "#3a3a3a"
TEXT = "#f5f5f5"
MUTED = "#929292"
MUTED2 = "#c4c4c4"
ACCENT = "#3b82f6"
ACCENT2 = "#1d4ed8"
RED = "#ef4444"
YELLOW = "#f59e0b"
GREEN = "#22c55e"
MONO = "'JetBrains Mono', 'Consolas', monospace"


class GridBackground(QWidget):
    """Ink-black background with a subtle white graph-paper grid."""

    GRID_SIZE = 28

    def __init__(self, parent=None, draw_grid=True):
        super().__init__(parent)
        self.draw_grid = draw_grid
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(BG))
        if not self.draw_grid:
            return
        painter.setPen(QPen(QColor(255, 255, 255, 48), 1))
        for x in range(0, self.width(), self.GRID_SIZE):
            painter.drawLine(x, 0, x, self.height())
        for y in range(0, self.height(), self.GRID_SIZE):
            painter.drawLine(0, y, self.width(), y)


class InsetShadowFrame(QFrame):
    """A black card with a restrained shadow falling inward from its edges."""

    def paintEvent(self, event):
        super().paintEvent(event)

        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if rect.width() <= 0 or rect.height() <= 0:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(rect, 7, 7)
        painter.setClipPath(path)

        depth = min(12.0, rect.width() / 4, rect.height() / 4)
        shadow = QColor(0, 0, 0, 100)
        clear = QColor(0, 0, 0, 0)

        gradient = QLinearGradient(rect.left(), rect.top(), rect.left(), rect.top() + depth)
        gradient.setColorAt(0, shadow)
        gradient.setColorAt(1, clear)
        painter.fillRect(QRectF(rect.left(), rect.top(), rect.width(), depth), gradient)

        gradient = QLinearGradient(rect.left(), rect.bottom() - depth, rect.left(), rect.bottom())
        gradient.setColorAt(0, clear)
        gradient.setColorAt(1, shadow)
        painter.fillRect(QRectF(rect.left(), rect.bottom() - depth, rect.width(), depth), gradient)

        gradient = QLinearGradient(rect.left(), rect.top(), rect.left() + depth, rect.top())
        gradient.setColorAt(0, shadow)
        gradient.setColorAt(1, clear)
        painter.fillRect(QRectF(rect.left(), rect.top(), depth, rect.height()), gradient)

        gradient = QLinearGradient(rect.right() - depth, rect.top(), rect.right(), rect.top())
        gradient.setColorAt(0, clear)
        gradient.setColorAt(1, shadow)
        painter.fillRect(QRectF(rect.right() - depth, rect.top(), depth, rect.height()), gradient)


QSS = f"""
QWidget {{
    background: transparent;
    color: {TEXT};
    font-family: 'Inter', 'Segoe UI', sans-serif;
    font-size: 13px;
}}

QMainWindow, QDialog {{
    background: {BG};
}}

/* ── Tabs ─────────────────────────────────────────────────────────── */
QTabWidget::pane {{
    border: 1px solid {BORDER};
    background: {SURFACE};
    border-radius: 8px;
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    color: {MUTED};
    padding: 10px 18px;
    margin-right: 4px;
    font-weight: 600;
    font-size: 12px;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{
    color: {TEXT};
    border-bottom: 2px solid {ACCENT};
}}
QTabBar::tab:hover:!selected {{
    color: {MUTED2};
}}

/* ── Cards / frames ───────────────────────────────────────────────── */
QFrame#card {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}

/* ── Tables ───────────────────────────────────────────────────────── */
QTableWidget {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 8px;
    gridline-color: {BORDER};
    selection-background-color: {SURFACE2};
    selection-color: {TEXT};
    alternate-background-color: {SURFACE};
}}
QHeaderView::section {{
    background: {BG};
    color: {MUTED};
    padding: 9px 12px;
    border: none;
    border-bottom: 1px solid {BORDER};
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
}}
QTableWidget::item {{
    background: {SURFACE};
    padding: 6px;
    border-bottom: 1px solid {BORDER};
}}

/* ── Lists ────────────────────────────────────────────────────────── */
QListWidget {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}
QListWidget::item {{
    padding: 10px;
    border-bottom: 1px solid {BORDER};
}}
QListWidget::item:selected {{
    background: {SURFACE2};
    color: {TEXT};
}}

/* ── Inputs ───────────────────────────────────────────────────────── */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {SURFACE2};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 8px 10px;
    color: {TEXT};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 1px solid {ACCENT};
}}
QCheckBox {{
    color: {TEXT};
    spacing: 8px;
}}

/* ── Buttons ──────────────────────────────────────────────────────── */
QPushButton {{
    background: {SURFACE2};
    color: {MUTED2};
    border: 1px solid {BORDER2};
    border-radius: 5px;
    padding: 5px 12px;
    font-weight: 600;
    font-size: 11px;
}}
QPushButton:hover {{
    background: {BORDER2};
    border-color: {ACCENT};
    color: {TEXT};
}}
QPushButton#primary {{
    background: {ACCENT2};
    border: 1px solid {ACCENT};
    color: white;
}}
QPushButton#primary:hover {{
    background: {ACCENT};
}}
QPushButton#danger {{
    background: transparent;
    border: 1px solid {RED};
    color: {RED};
}}

/* ── Labels ───────────────────────────────────────────────────────── */
QLabel {{
    background: transparent;
}}
QLabel#h1 {{
    font-size: 20px;
    font-weight: 700;
    color: {TEXT};
}}
QLabel#h2 {{
    font-size: 13px;
    font-weight: 600;
    color: {MUTED2};
    text-transform: uppercase;
    letter-spacing: 0.5px;
}}
QLabel#muted {{
    color: {MUTED};
    font-size: 12px;
}}
QLabel#error {{
    background: rgba(239, 68, 68, 25);
    border: 1px solid {RED};
    border-radius: 6px;
    color: {RED};
    font-size: 12px;
    padding: 8px 10px;
}}
QLabel#metricValue {{
    font-size: 30px;
    font-weight: 700;
    font-family: {MONO};
}}

/* ── Sidebar nav items (mirrors web dashboard's .nav-item) ──────────── */
QPushButton#navItem {{
    background: transparent;
    color: {MUTED2};
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 8px 10px;
    text-align: left;
    font-weight: 500;
    font-size: 12px;
}}
QPushButton#navItem:hover {{
    background: {SURFACE2};
    color: {TEXT};
}}
QPushButton#navItem:checked {{
    background: rgba(59, 130, 246, 40);
    color: {ACCENT};
    border: 1px solid rgba(59, 130, 246, 60);
}}

/* ── Scrollbars ───────────────────────────────────────────────────── */
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
}}
QScrollBar::handle:vertical {{
    background: {BORDER};
    border-radius: 4px;
    min-height: 24px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
"""