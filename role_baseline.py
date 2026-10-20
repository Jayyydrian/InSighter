"""Role-aware behavioral baselines used by the risk scoring layer."""

import pandas as pd

from database import connect
from sector_config import DEFAULT_SECTOR, get_active_sector

ROLE_FIELDS = [
    "files_accessed",
    "data_transferred_mb",
    "failed_logins",
    "off_hours_access",
]

ROLE_BASELINE_FEATURES = {
    "files_accessed": {
        "label": "Average files accessed per event",
        "unit": "files/event",
        "min": 0.0,
        "max": 155.0,
    },
    "data_transferred_mb": {
        "label": "Average data transferred per event",
        "unit": "MB/event",
        "min": 0.0,
        "max": 1202.0,
    },
    "failed_logins": {
        "label": "Average failed logins per event",
        "unit": "attempts/event",
        "min": 0.0,
        "max": 13.5,
    },
    "off_hours_access": {
        "label": "Off-hours event fraction",
        "unit": "fraction of events",
        "min": 0.0,
        "max": 1.0,
    },
}

# Per-event p99s (private school / UAV): files 103.22 / 81.00,
# transfer MB 800.7861 / 529.8437, failed logins 9 / 6.81,
# and off-hours 1 / 1. Numeric maxima use 1.5x the largest p99,
# rounded up where needed; off-hours is capped by its indicator range [0, 1].


def attach_roles(df):
    result = df.copy()
    if "role" not in result.columns:
        result["role"] = "unknown"
    result["role"] = result["role"].fillna("unknown").replace("", "unknown")
    return result


def build_role_baseline_features():
    """Return the declarable-role baseline metadata used by the admin wizard."""
    features = {}
    for key, options in ROLE_BASELINE_FEATURES.items():
        features[key] = {
            "key": key,
            "label": options["label"],
            "unit": options["unit"],
            "min": float(options["min"]),
            "max": float(options["max"]),
            "default_source": "mean of non-anomalous activity by role",
        }
    return features


def _sector_baseline_status(sector=None):
    sector = sector or get_active_sector() or DEFAULT_SECTOR
    conn = connect()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS baseline_setup_status (
            sector TEXT PRIMARY KEY,
            completed INTEGER NOT NULL DEFAULT 0,
            completed_by TEXT,
            completed_at TEXT
        )
        """
    )
    conn.commit()
    row = conn.execute(
        "SELECT completed FROM baseline_setup_status WHERE sector = ?",
        (sector,),
    ).fetchone()
    conn.close()
    return bool(row and row[0])


def get_declared_role_baselines(sector=None):
    sector = sector or get_active_sector() or DEFAULT_SECTOR
    if not _sector_baseline_status(sector):
        return {}
    conn = connect()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS role_baselines (
            sector TEXT,
            role TEXT,
            feature TEXT,
            value REAL,
            updated_by TEXT,
            updated_at TEXT,
            PRIMARY KEY (sector, role, feature)
        )
        """
    )
    conn.commit()
    rows = conn.execute(
        "SELECT role, feature, value FROM role_baselines WHERE sector = ?",
        (sector,),
    ).fetchall()
    conn.close()
    values = {}
    for role, feature, value in rows:
        values.setdefault(str(role), {})[str(feature)] = float(value)
    return values


def get_effective_role_baselines(df, sector=None):
    baselines, overall = calculate_role_baselines(df)
    sector = sector or get_active_sector() or DEFAULT_SECTOR
    overrides = get_declared_role_baselines(sector)
    effective = {}
    for role, defaults in baselines.items():
        values = dict(defaults)
        for field in ROLE_FIELDS:
            if role in overrides and field in overrides[role]:
                values[field] = overrides[role][field]
        effective[role] = values
    return effective, overall


def calculate_role_baselines(df):
    result = attach_roles(df)
    if "is_anomaly" in result.columns:
        normal = result[result["is_anomaly"] == 0]
    else:
        normal = result
    if normal.empty:
        normal = result
    baselines = normal.groupby("role")[ROLE_FIELDS].mean().to_dict(orient="index")
    overall = normal[ROLE_FIELDS].mean().to_dict()
    return baselines, overall


def attach_baseline_deviations(df, baselines, overall):
    result = attach_roles(df)

    deviations = []
    for _, row in result.iterrows():
        baseline = baselines.get(row["role"], overall)
        ratios = []
        for field in ROLE_FIELDS:
            expected = float(baseline.get(field, 0))
            actual = float(row[field])
            if expected > 0:
                ratios.append(abs(actual - expected) / expected)
            elif actual > 0:
                # Zero is a valid baseline; handle it without dividing by zero.
                ratios.append(1.0)
        deviations.append(
            min(100.0, (sum(ratios) / len(ratios)) * 50) if ratios else 0.0
        )

    result["role_baseline_score"] = deviations
    return result
