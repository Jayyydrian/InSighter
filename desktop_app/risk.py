"""Presentation helpers shared by desktop risk displays."""

from risk_thresholds import risk_level_for

RISK_COLORS = {
    "HIGH": "#ef4444",
    "MEDIUM": "#f59e0b",
    "LOW": "#10b981",
}


def risk_color_for_level(level):
    return RISK_COLORS.get(level, "#e2e8f0")
