"""Role-aware behavioral baselines used by the risk scoring layer."""

import pandas as pd

ROLE_FIELDS = [
    "files_accessed",
    "data_transferred_mb",
    "failed_logins",
    "off_hours_access",
]


def attach_roles(df):
    result = df.copy()
    if "role" not in result.columns:
        result["role"] = "unknown"
    result["role"] = result["role"].fillna("unknown").replace("", "unknown")
    return result


def calculate_role_baselines(df):
    result = attach_roles(df)
    normal = result[result.get("is_anomaly", 0) == 0]
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
                ratios.append(1.0)
        deviations.append(
            min(100.0, (sum(ratios) / len(ratios)) * 50) if ratios else 0.0
        )

    result["role_baseline_score"] = deviations
    return result
