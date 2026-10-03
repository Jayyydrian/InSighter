import random
import os
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.svm import OneClassSVM
from sklearn.preprocessing import StandardScaler
from privacy import sanitize_event
from role_baseline import attach_baseline_deviations, attach_roles, calculate_role_baselines
from database import connect
from sector_config import DEFAULT_SECTOR, get_active_sector, get_sector_config, get_sector_roster
from simulation import activity_values

# Excluded: no current AD/Google Workspace/Microsoft 365 integration supplies browsing-derived
# signal; validated as beneficial in CERT evaluation (see cert_score.py), reserved for future browsing integration.
FEATURES = ["login_hour", "files_accessed", "data_transferred_mb", "failed_logins", "off_hours_access"]
SKEWED_FEATURES = {"files_accessed", "data_transferred_mb", "exfil_domain_hits", "job_site_hits"}

# Ensemble weights per Chapter 3 design: Final Risk Score = 0.6*IF + 0.4*OC-SVM
IF_WEIGHT    = 0.6
OCSVM_WEIGHT = 0.4
CONTAMINATION = 0.08  # expected proportion of anomalous logs, shared by both models
MIN_ROLE_SAMPLES = 20
MIN_ROLE_USERS = 2
MODEL_ESTIMATORS = max(40, int(os.environ.get("INSIGHTER_MODEL_ESTIMATORS", "80")))
CALIBRATION_LOW_QUANTILE = 0.05
CALIBRATION_HIGH_QUANTILE = 0.95


def _scale_0_100(raw, reference=None):
    """Calibrate anomaly scores against a reference distribution on a 0-100 scale."""
    raw = np.asarray(raw, dtype=float)
    reference = raw if reference is None else np.asarray(reference, dtype=float)
    if raw.size == 0 or reference.size == 0:
        return np.zeros_like(raw)
    lo, hi = np.quantile(
        reference,
        [CALIBRATION_LOW_QUANTILE, CALIBRATION_HIGH_QUANTILE],
    )
    if hi - lo < 1e-9:
        lo, hi = reference.min(), reference.max()
    if hi - lo < 1e-9:
        return np.zeros_like(raw)
    return np.clip((raw - lo) / (hi - lo) * 100, 0, 100)


def _prepare_model_features(frame, feature_list=None):
    """Apply log1p to skewed volumes, then leave scaling to the existing scaler."""
    prepared = frame.reindex(columns=feature_list or FEATURES, fill_value=0).copy()
    prepared = prepared.fillna(0)
    for feature in SKEWED_FEATURES.intersection(prepared.columns):
        # StandardScaler already handles centering/scaling; log1p first reduces heavy-tail dominance.
        prepared[feature] = np.log1p(prepared[feature].clip(lower=0))
    return prepared


def _fit_group_models(group, score_group=None, contamination=CONTAMINATION, ocsvm_nu=None, feature_list=None):
    score_group = group if score_group is None else score_group
    scaler = StandardScaler().fit(_prepare_model_features(group, feature_list))
    X = scaler.transform(_prepare_model_features(group, feature_list))
    score_X = scaler.transform(_prepare_model_features(score_group, feature_list))

    if_model = IsolationForest(
        contamination=contamination, random_state=42, n_estimators=MODEL_ESTIMATORS
    )
    if_model.fit(X)
    if_reference = -if_model.decision_function(X)
    if_scores = _scale_0_100(-if_model.decision_function(score_X), if_reference)
    anomalies = (if_model.predict(score_X) == -1).astype(int)

    ocsvm_model = OneClassSVM(kernel="rbf", nu=ocsvm_nu or contamination, gamma="auto")
    ocsvm_model.fit(X)
    ocsvm_reference = -ocsvm_model.decision_function(X)
    ocsvm_scores = _scale_0_100(-ocsvm_model.decision_function(score_X), ocsvm_reference)
    return if_scores, anomalies, ocsvm_scores


def _run_ensemble(df, contamination=CONTAMINATION, ocsvm_nu=None, fit_df=None, feature_list=None):
    """Fit role-specific models when enough role data exists, with fallback."""
    df = attach_roles(df)
    training = attach_roles(fit_df if fit_df is not None else df)
    fallback = _fit_group_models(training, df, contamination, ocsvm_nu, feature_list)
    df["if_score"], df["is_anomaly"], df["ocsvm_score"] = fallback
    training["is_anomaly"] = _fit_group_models(
        training, contamination=contamination, ocsvm_nu=ocsvm_nu, feature_list=feature_list
    )[1]

    for role, group in training.groupby("role"):
        if role == "unknown" or group["user"].nunique() < MIN_ROLE_USERS:
            continue
        if len(group) < MIN_ROLE_SAMPLES:
            continue
        target = df[df["role"] == role]
        if target.empty:
            continue
        scores = _fit_group_models(group, target, contamination, ocsvm_nu, feature_list)
        df.loc[target.index, "if_score"] = scores[0]
        df.loc[target.index, "is_anomaly"] = scores[1]
        df.loc[target.index, "ocsvm_score"] = scores[2]
        if fit_df is None:
            training.loc[group.index, "is_anomaly"] = scores[1]

    baselines, overall = calculate_role_baselines(training)
    df = attach_baseline_deviations(df, baselines, overall)
    df["final_score"] = (
        IF_WEIGHT * df["if_score"]
        + OCSVM_WEIGHT * df["ocsvm_score"]
        + 0.1 * df["role_baseline_score"]
    ).clip(upper=100)
    return df


def score_users():
    sector = get_active_sector() or DEFAULT_SECTOR
    allowed_roles = set(get_sector_config(sector)["roles"])
    conn = connect()
    df = pd.read_sql("SELECT * FROM logs", conn)
    conn.close()

    if "role" in df.columns:
        df = df[df["role"].isin(allowed_roles)].copy()
    if df.empty:
        return []

    df = _run_ensemble(df)

    summary = df.groupby("user").agg(
        total_logs=("user", "count"),
        anomalous_logs=("is_anomaly", "sum"),
        avg_files=("files_accessed", "mean"),
        avg_transfer=("data_transferred_mb", "mean"),
        avg_failed_logins=("failed_logins", "mean"),
        off_hours=("off_hours_access", "sum"),
        if_score=("if_score", "mean"),
        ocsvm_score=("ocsvm_score", "mean"),
        risk_score=("final_score", "mean"),
        role=("role", "first"),
        role_baseline_score=("role_baseline_score", "mean"),
    ).reset_index()

    summary["risk_score"]  = summary["risk_score"].round(1)
    summary["if_score"]    = summary["if_score"].round(1)
    summary["ocsvm_score"] = summary["ocsvm_score"].round(1)
    summary["risk_level"] = summary["risk_score"].apply(
        lambda x: "HIGH" if x >= 45 else ("MEDIUM" if x >= 30 else "LOW")
    )
    summary["avg_files"]         = summary["avg_files"].round(1)
    summary["avg_transfer"]      = summary["avg_transfer"].round(1)
    summary["avg_failed_logins"] = summary["avg_failed_logins"].round(1)
    summary["role_baseline_score"] = summary["role_baseline_score"].round(1)

    return summary.sort_values("risk_score", ascending=False).to_dict(orient="records")


def get_recent_alerts():
    sector = get_active_sector() or DEFAULT_SECTOR
    sector_config = get_sector_config(sector)
    allowed_roles = set(sector_config["roles"])
    conn = connect()
    df = pd.read_sql("SELECT * FROM logs ORDER BY id DESC LIMIT 600", conn)
    conn.close()

    if "role" in df.columns:
        df = df[df["role"].isin(allowed_roles)].copy()
    if df.empty:
        return []

    df = _run_ensemble(df)
    # An alert fires if either model treats the row as anomalous, or the blended score is high
    ocsvm_cut = np.percentile(df["ocsvm_score"], 100 * (1 - CONTAMINATION))
    df["ocsvm_flag"] = (df["ocsvm_score"] >= ocsvm_cut).astype(int)
    df["is_anomaly"] = ((df["is_anomaly"] == 1) | (df["ocsvm_flag"] == 1)).astype(int)

    alerts = df[df["is_anomaly"] == 1].copy()

    def describe(row):
        r = []
        if row["login_hour"] < 6 or row["login_hour"] > 21:
            r.append("off-hours mission-console access" if sector == "uav_disaster_response" else "off-hours login")
        if row["files_accessed"] > 40:
            noun = "disaster survivor records" if sector == "uav_disaster_response" else "files"
            r.append(f"accessed {int(row['files_accessed'])} {noun}")
        if row["data_transferred_mb"] > 200:
            noun = "of triage data" if sector == "uav_disaster_response" else ""
            r.append(f"transferred {row['data_transferred_mb']:.0f} MB {noun}".strip())
        if row["failed_logins"] > 3:
            noun = "mission-control authentication attempts" if sector == "uav_disaster_response" else "failed logins"
            r.append(f"{int(row['failed_logins'])} {noun}")
        return ", ".join(r) if r else "unusual activity pattern"

    alerts = alerts.copy()
    alerts["description"] = alerts.apply(describe, axis=1)
    alerts["severity"] = alerts.apply(
        lambda r: "CRITICAL" if (r["data_transferred_mb"] > 600 or r["files_accessed"] > 90)
                  else ("HIGH" if (r["data_transferred_mb"] > 400 or r["files_accessed"] > 70)
                  else "MEDIUM"), axis=1
    )

    def flagged_by(row):
        by_if     = row["is_anomaly"] == 1
        by_ocsvm  = row["ocsvm_flag"] == 1
        if by_if and by_ocsvm:
            return "IF + OC-SVM"
        if by_ocsvm:
            return "OC-SVM"
        return "IF"

    alerts["flagged_by"]  = alerts.apply(flagged_by, axis=1)
    alerts["if_score"]    = alerts["if_score"].round(1)
    alerts["ocsvm_score"] = alerts["ocsvm_score"].round(1)
    alerts["final_score"] = alerts["final_score"].round(1)

    return alerts[["id","user","role","login_hour","off_hours_access","data_category","files_accessed","data_transferred_mb",
                   "failed_logins","description","severity",
                   "flagged_by","if_score","ocsvm_score","final_score"]].head(25).to_dict(orient="records")


def _live_simulation_roster(conn, sector):
    """Return the canonical generated roster, independent of stale stored rows."""
    return get_sector_roster(sector)


def inject_live_event():
    """Randomly inject a new log row to simulate live fluctuation.

    The roster and sector-specific activity profile are derived from the
    active sector; anomalous behavior is selected probabilistically per tick.
    """
    conn = connect()
    sector = get_active_sector(conn) or DEFAULT_SECTOR
    sector_config = get_sector_config(sector)
    categories = list(sector_config["data_categories"])
    roster = _live_simulation_roster(conn, sector)
    user, role = random.choice(roster)
    anomalous = random.random() < 0.25
    values = activity_values(sector, anomalous, random)
    row = (user, *values)
    category = categories[-1 if anomalous else 0]

    event = dict(zip(
        ("user", "login_hour", "files_accessed", "data_transferred_mb", "failed_logins", "off_hours_access"),
        row,
    ))
    event["role"] = role
    sanitized = sanitize_event(event)
    conn.execute(
        """INSERT INTO logs
        (user,login_hour,files_accessed,data_transferred_mb,failed_logins,
         off_hours_access,role,source,payload_encrypted,data_category,ingested_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
        tuple(sanitized[key] for key in (
            "user", "login_hour", "files_accessed", "data_transferred_mb",
            "failed_logins", "off_hours_access", "role", "source", "payload_encrypted",
        )) + (category,),
    )
    conn.commit()
    conn.close()
    return {
        "user": user,
        "role": role,
        "anomalous": anomalous,
        "activity": dict(zip(
            ("login_hour", "files_accessed", "data_transferred_mb", "failed_logins", "off_hours_access"),
            values,
        )),
    }
