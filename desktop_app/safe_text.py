"""Helpers for displaying untrusted strings in Qt widgets."""

import html

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel


def esc(value):
    return html.escape(str(value if value is not None else ""), quote=True)


def plain_label(text="", parent=None):
    label = QLabel("" if text is None else str(text), parent)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setOpenExternalLinks(False)
    return label
