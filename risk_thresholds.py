"""Shared validation and risk-level mapping for deployment thresholds."""

import logging
import math
import numbers
import threading

from database import connect, sqlite3

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLDS = (45.0, 30.0)
_warning_lock = threading.Lock()
_fallback_warning_logged = False


def validate_thresholds(high, medium):
    if (
        isinstance(high, bool)
        or isinstance(medium, bool)
        or not isinstance(high, numbers.Real)
        or not isinstance(medium, numbers.Real)
    ):
        raise ValueError("threshold_high and threshold_medium must be numeric values.")

    high = float(high)
    medium = float(medium)
    if not math.isfinite(high) or not math.isfinite(medium):
        raise ValueError("threshold_high and threshold_medium must be finite numbers.")
    if not 0 < medium < high <= 100:
        raise ValueError(
            "Thresholds must satisfy 0 < threshold_medium < threshold_high <= 100."
        )
    return high, medium


def _warn_fallback(reason):
    global _fallback_warning_logged
    with _warning_lock:
        if _fallback_warning_logged:
            return
        logger.warning(
            "Invalid or missing deployment risk thresholds (%s); using defaults "
            "threshold_high=%s, threshold_medium=%s.",
            reason,
            DEFAULT_THRESHOLDS[0],
            DEFAULT_THRESHOLDS[1],
        )
        _fallback_warning_logged = True


def get_thresholds(conn=None):
    owns_connection = conn is None
    connection = connect() if owns_connection else conn
    reason = None
    try:
        try:
            row = connection.execute(
                "SELECT threshold_high, threshold_medium "
                "FROM deployment_config WHERE id = 1"
            ).fetchone()
        except sqlite3.Error:
            reason = "deployment configuration is unavailable"
            row = None

        if row is None:
            reason = reason or "deployment configuration is missing"
        else:
            try:
                return validate_thresholds(row[0], row[1])
            except ValueError as exc:
                reason = str(exc)
    finally:
        if owns_connection:
            connection.close()

    _warn_fallback(reason)
    return DEFAULT_THRESHOLDS


def risk_level_for(score, high, medium):
    if score >= high:
        return "HIGH"
    if score >= medium:
        return "MEDIUM"
    return "LOW"
