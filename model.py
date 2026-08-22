import random
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.svm import OneClassSVM
from sklearn.preprocessing import StandardScaler
from privacy import sanitize_event
from role_baseline import attach_baseline_deviations, attach_roles, calculate_role_baselines
from database import connect

FEATURES = ["login_hour", "files_accessed", "data_transferred_mb", "failed_logins", "off_hours_access"]

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


def _fit_group_models(group):
    X = StandardScaler().fit_transform(group[FEATURES])

    if_model = IsolationForest(contamination=CONTAMINATION, random_state=42, n_estimators=200)
    if_model.fit(X)
    if_scores = _scale_0_100(-if_model.decision_function(X))
    anomalies = (if_model.predict(X) == -1).astype(int)

    ocsvm_model = OneClassSVM(kernel="rbf", nu=CONTAMINATION, gamma="auto")
    ocsvm_model.fit(X)
    ocsvm_scores = _scale_0_100(-ocsvm_model.decision_function(X))
    return if_scores, anomalies, ocsvm_scores


def _run_ensemble(df):
    """Fit role-specific models when enough role data exists, with fallback."""
    df = attach_roles(df)
    fallback = _fit_group_models(df)
    df["if_score"], df["is_anomaly"], df["ocsvm_score"] = fallback

    for role, group in df.groupby("role"):
        if role == "unknown" or group["user"].nunique() < MIN_ROLE_USERS:
            continue
        if len(group) < MIN_ROLE_SAMPLES:
            continue
        scores = _fit_group_models(group)
        df.loc[group.index, "if_score"] = scores[0]
        df.loc[group.index, "is_anomaly"] = scores[1]
        df.loc[group.index, "ocsvm_score"] = scores[2]

    baselines, overall = calculate_role_baselines(df)
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
    """Randomly inject a new log row to simulate live fluctuation."""
    conn = connect()
    users = ["alice", "bob", "charlie", "diana", "eve"]
    weights = [0.15, 0.15, 0.15, 0.15, 0.40]   # eve more likely to spike
    user = random.choices(users, weights=weights)[0]

    roll = random.random()
    if user == "eve":
        if roll < 0.6:   # anomalous
            row = (user, random.choice([1,2,3,22,23]),
                   random.randint(55,130), round(random.uniform(300,1100),2),
                   random.randint(4,10), 1)
        else:            # occasionally normal-ish
            row = (user, random.randint(9,17),
                   random.randint(5,25), round(random.uniform(10,80),2),
                   random.randint(0,2), 0)
    else:
        if roll < 0.08:  # small chance normal user spikes
            row = (user, random.choice([0,1,22,23]),
                   random.randint(30,60), round(random.uniform(100,400),2),
                   random.randint(3,6), 1)
        else:
            row = (user, random.randint(8,18),
                   random.randint(1,15), round(random.uniform(1,40),2),
                   random.randint(0,1), 0)

    sanitized = sanitize_event(dict(zip(
        ("user", "login_hour", "files_accessed", "data_transferred_mb", "failed_logins", "off_hours_access"),
        row,
    )))
    conn.execute(
        """INSERT INTO logs
        (user,login_hour,files_accessed,data_transferred_mb,failed_logins,
         off_hours_access,role,source,payload_encrypted,ingested_at)
        VALUES (?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
        tuple(sanitized[key] for key in (
            "user", "login_hour", "files_accessed", "data_transferred_mb",
            "failed_logins", "off_hours_access", "role", "source", "payload_encrypted",
        )),
    )
    conn.commit()
    conn.close()
