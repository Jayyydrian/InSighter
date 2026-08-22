BG = "#0a0e1a"
SURFACE = "#111827"
SURFACE2 = "#1a2235"
BORDER = "#1e2d45"
BORDER2 = "#253550"
TEXT = "#e2e8f0"
MUTED = "#64748b"
MUTED2 = "#94a3b8"
ACCENT = "#3b82f6"
ACCENT2 = "#1d4ed8"
RED = "#ef4444"
YELLOW = "#f59e0b"
GREEN = "#22c55e"
MONO = "'JetBrains Mono', 'Consolas', monospace"

QSS = f"""
QWidget {{
    background: {BG};
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