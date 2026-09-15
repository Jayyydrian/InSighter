import random
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.svm import OneClassSVM
from sklearn.preprocessing import StandardScaler
from privacy import sanitize_event
from role_baseline import attach_baseline_deviations, attach_roles, calculate_role_baselines
from database import connect
from sector_config import (
    DEFAULT_SECTOR,
    get_active_sector,
    get_anomalous_demo_user,
    get_demo_roster,
    get_sector_config,
)

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


def _scale_0_100(raw):
    """Min-max scale a raw anomaly-score array to a 0-100 range (100 = most anomalous)."""
    raw = np.asarray(raw, dtype=float)
    lo, hi = raw.min(), raw.max()
    if hi - lo < 1e-9:
        return np.zeros_like(raw)
    return (raw - lo) / (hi - lo) * 100


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

    if_model = IsolationForest(contamination=contamination, random_state=42, n_estimators=200)
    if_model.fit(X)
    if_scores = _scale_0_100(-if_model.decision_function(score_X))
    anomalies = (if_model.predict(score_X) == -1).astype(int)

    ocsvm_model = OneClassSVM(kernel="rbf", nu=ocsvm_nu or contamination, gamma="auto")
    ocsvm_model.fit(X)
    ocsvm_scores = _scale_0_100(-ocsvm_model.decision_function(score_X))
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
    conn = connect()
    df = pd.read_sql("SELECT * FROM logs", conn)
    conn.close()

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
    conn = connect()
    df = pd.read_sql("SELECT * FROM logs ORDER BY id DESC LIMIT 600", conn)
    conn.close()

    df = _run_ensemble(df)
    # An alert fires if either model treats the row as anomalous, or the blended score is high
    ocsvm_cut = np.percentile(df["ocsvm_score"], 100 * (1 - CONTAMINATION))
    df["ocsvm_flag"] = (df["ocsvm_score"] >= ocsvm_cut).astype(int)
    df["is_anomaly"] = ((df["is_anomaly"] == 1) | (df["ocsvm_flag"] == 1)).astype(int)

    alerts = df[df["is_anomaly"] == 1].copy()

    def describe(row):
        r = []
        if row["login_hour"] < 6 or row["login_hour"] > 21:
            r.append("off-hours login")
        if row["files_accessed"] > 40:
            r.append(f"accessed {int(row['files_accessed'])} files")
        if row["data_transferred_mb"] > 200:
            r.append(f"transferred {row['data_transferred_mb']:.0f} MB")
        if row["failed_logins"] > 3:
            r.append(f"{int(row['failed_logins'])} failed logins")
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

    return alerts[["user","login_hour","files_accessed","data_transferred_mb",
                   "failed_logins","description","severity",
                   "flagged_by","if_score","ocsvm_score","final_score"]].head(25).to_dict(orient="records")


def inject_live_event():
    """Randomly inject a new log row to simulate live fluctuation.

    The roster and the "high-risk" profile it weights toward are both
    derived from the active sector's configuration, rather than a fixed
    cast of demo usernames.
    """
    conn = connect()
    sector = get_active_sector(conn) or DEFAULT_SECTOR
    sector_config = get_sector_config(sector)
    categories = list(sector_config["data_categories"])
    roster = list(get_demo_roster(sector).items())
    anomalous_user = get_anomalous_demo_user(sector)

    n = len(roster)
    # The active sector wins immediately; stale rows remain historical and age out,
    # rather than allowing a previous sector's users to drive new simulation data.
    weights = [0.4 if user == anomalous_user else 0.6 / (n - 1) for user, _ in roster] if n > 1 else [1.0]
    user, role = random.choices(roster, weights=weights)[0]
    is_watch_profile = user == anomalous_user

    roll = random.random()
    if is_watch_profile:
        if roll < 0.6:   # anomalous
            row = (user, random.choice([1, 2, 3, 22, 23]),
                   random.randint(55, 130), round(random.uniform(300, 1100), 2),
                   random.randint(4, 10), 1)
            category = categories[-1]
        else:             # occasionally normal-ish
            row = (user, random.randint(9, 17),
                   random.randint(5, 25), round(random.uniform(10, 80), 2),
                   random.randint(0, 2), 0)
            category = categories[0]
    else:
        if roll < 0.08:  # small chance a non-watch user spikes
            row = (user, random.choice([0, 1, 22, 23]),
                   random.randint(30, 60), round(random.uniform(100, 400), 2),
                   random.randint(3, 6), 1)
            category = categories[-1]
        else:
            row = (user, random.randint(8, 18),
                   random.randint(1, 15), round(random.uniform(1, 40), 2),
                   random.randint(0, 1), 0)
            category = categories[0]

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
