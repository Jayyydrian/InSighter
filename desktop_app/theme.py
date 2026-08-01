BG = "#0a0e1a"
SURFACE = "#111827"
SURFACE2 = "#1a2235"
BORDER = "#1e2d45"
TEXT = "#e2e8f0"
MUTED = "#64748b"
MUTED2 = "#94a3b8"
ACCENT = "#3b82f6"
ACCENT2 = "#1d4ed8"
RED = "#ef4444"
YELLOW = "#f59e0b"
GREEN = "#22c55e"

QSS = f"""
QWidget {{
    background: {BG};
    color: {TEXT};
    font-family: 'Segoe UI', 'Inter', sans-serif;
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
    border-radius: 10px;
}}

/* ── Tables ───────────────────────────────────────────────────────── */
QTableWidget {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 8px;
    gridline-color: {BORDER};
    selection-background-color: {SURFACE2};
    selection-color: {TEXT};
}}
QHeaderView::section {{
    background: {SURFACE2};
    color: {MUTED2};
    padding: 8px;
    border: none;
    border-bottom: 1px solid {BORDER};
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
}}
QTableWidget::item {{
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
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 8px 16px;
    font-weight: 600;
    font-size: 12px;
}}
QPushButton:hover {{
    border-color: {ACCENT};
}}
QPushButton#primary {{
    background: {ACCENT};
    border: none;
    color: white;
}}
QPushButton#primary:hover {{
    background: {ACCENT2};
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
    color: {RED};
    font-size: 12px;
}}
QLabel#metricValue {{
    font-size: 30px;
    font-weight: 700;
    font-family: 'Consolas', monospace;
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