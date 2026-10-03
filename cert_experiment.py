"""CERT-only evaluation experiments.

This module deliberately does not change the live model or dashboard.  It provides
held-out repeated evaluation, raw-score calibration, and a chunked user-day view
for CERT r4.2.
"""

from __future__ import annotations

import argparse
import gc
import os
from collections import defaultdict
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.metrics import average_precision_score, precision_score, recall_score, roc_auc_score
from sklearn.model_selection import StratifiedKFold, StratifiedShuffleSplit
from sklearn.preprocessing import StandardScaler
from sklearn.svm import OneClassSVM

from cert_adapter import _load_roles, load_ground_truth


# CERT-only feature sets.  model.FEATURES is intentionally not modified.
BASE_FEATURES = ["login_hour", "files_accessed", "data_transferred_mb", "off_hours_access"]
CERT_EXTRA_FEATURES = ["exfil_domain_hits", "job_site_hits"]
DAILY_FEATURES = [
    "after_hours_logons", "distinct_pcs", "usb_connects", "usb_after_hours",
    "files_accessed", "email_count", "external_recipients", "bcc_count",
    "attachment_count", "email_size_mb",
]
HTTP_FEATURES = ["exfil_domain_hits", "job_site_hits"]
DEFAULT_GRID = ((0.05, 0.08), (0.08, 0.08), (0.12, 0.12))
DEFAULT_OCSVM_LIMIT = 20_000


def _empty_day() -> dict[str, object]:
    return {
        "after_hours_logons": 0, "distinct_pcs": set(), "usb_connects": 0,
        "usb_after_hours": 0, "files_accessed": 0, "email_count": 0,
        "external_recipients": 0, "bcc_count": 0, "attachment_count": 0,
        "email_size_mb": 0.0, "exfil_domain_hits": 0, "job_site_hits": 0,
    }


def _day_values(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    timestamps = pd.to_datetime(frame["date"], errors="coerce")
    return frame["user"].astype(str), timestamps.dt.strftime("%Y-%m-%d")


def _recipient_count(value: object, own_domain: str = "dtaa.com") -> int:
    if pd.isna(value) or not str(value).strip():
        return 0
    return sum("@" in address and not address.strip().lower().endswith(f"@{own_domain}")
               for address in str(value).split(";"))


def _read_chunks(path: Path, columns: list[str], chunk_size: int) -> Iterable[pd.DataFrame]:
    if not path.is_file():
        return
    header = pd.read_csv(path, nrows=0)
    available = {str(name).lower(): name for name in header.columns}
    usecols = [available[name] for name in columns if name in available]
    if "user" not in {name.lower() for name in usecols} or "date" not in {name.lower() for name in usecols}:
        raise ValueError(f"{path.name} must contain user and date columns")
    yield from pd.read_csv(path, usecols=usecols, chunksize=chunk_size, low_memory=False)


def build_user_day_table(cert_dir: Path | str, *, include_http: bool = True, chunk_size: int = 100_000) -> pd.DataFrame:
    """Aggregate CERT event streams into user-day rows without loading raw files."""
    root = Path(cert_dir).expanduser()
    days: dict[tuple[str, str], dict[str, object]] = defaultdict(_empty_day)

    for frame in _read_chunks(root / "logon.csv", ["user", "date", "pc"], chunk_size):
        users, dates = _day_values(frame)
        for user, date, pc, timestamp in zip(users, dates, frame.get("pc", ""), pd.to_datetime(frame["date"], errors="coerce")):
            if pd.isna(date) or pd.isna(timestamp):
                continue
            row = days[(user, date)]
            row["distinct_pcs"].add(str(pc))
            row["after_hours_logons"] += int(timestamp.hour < 6 or timestamp.hour > 20)

    for frame in _read_chunks(root / "device.csv", ["user", "date", "pc", "activity"], chunk_size):
        users, dates = _day_values(frame)
        for user, date, pc, activity, timestamp in zip(users, dates, frame.get("pc", ""), frame.get("activity", ""), pd.to_datetime(frame["date"], errors="coerce")):
            if pd.isna(date) or pd.isna(timestamp):
                continue
            row = days[(user, date)]
            row["distinct_pcs"].add(str(pc))
            if str(activity).strip().lower() == "connect":
                row["usb_connects"] += 1
                row["usb_after_hours"] += int(timestamp.hour < 6 or timestamp.hour > 20)

    for frame in _read_chunks(root / "file.csv", ["user", "date"], chunk_size):
        users, dates = _day_values(frame)
        for user, date in zip(users, dates):
            if not pd.isna(date):
                days[(user, date)]["files_accessed"] += 1

    email_columns = ["user", "date", "pc", "to", "cc", "bcc", "size", "attachments"]
    for frame in _read_chunks(root / "email.csv", email_columns, chunk_size):
        users, dates = _day_values(frame)
        for index, (user, date) in enumerate(zip(users, dates)):
            if pd.isna(date):
                continue
            row = days[(user, date)]
            row["email_count"] += 1
            row["external_recipients"] += sum(
                _recipient_count(frame[column].iloc[index]) for column in ("to", "cc") if column in frame
            )
            row["bcc_count"] += int(bool(str(frame["bcc"].iloc[index]).strip())) if "bcc" in frame else 0
            if "attachments" in frame and pd.notna(frame["attachments"].iloc[index]):
                row["attachment_count"] += int(float(frame["attachments"].iloc[index]) or 0)
            if "size" in frame and pd.notna(frame["size"].iloc[index]):
                row["email_size_mb"] += float(frame["size"].iloc[index]) / (1024 * 1024)

    if include_http:
        for frame in _read_chunks(root / "http.csv", ["user", "date", "url"], chunk_size):
            users, dates = _day_values(frame)
            for user, date, url in zip(users, dates, frame.get("url", "")):
                if pd.isna(date):
                    continue
                normalized = str(url).lower()
                row = days[(user, date)]
                row["exfil_domain_hits"] += int("wikileaks.org" in normalized)
                row["job_site_hits"] += int(any(value in normalized for value in ("indeed", "linkedin.com/jobs", "monster.com", "careerbuilder")))

    records = []
    for (user, date), values in days.items():
        record = {"user": user, "date": date}
        record.update(values)
        record["distinct_pcs"] = len(record["distinct_pcs"])
        records.append(record)
    return pd.DataFrame(records).fillna(0)


def apply_insider_day_labels(days: pd.DataFrame, answers: pd.DataFrame) -> pd.DataFrame:
    """Label only days inside the insider's inclusive answer-key window."""
    result = days.copy()
    result["date"] = pd.to_datetime(result["date"], errors="coerce").dt.normalize()
    labels = answers.copy()
    labels["user"] = labels["user"].astype(str)
    labels["start"] = pd.to_datetime(labels["start"], errors="coerce")
    labels["end"] = pd.to_datetime(labels["end"], errors="coerce")
    result["actual_label"] = 0
    for row in labels.itertuples(index=False):
        if pd.isna(row.start) or pd.isna(row.end):
            continue
        mask = (result["user"] == row.user) & (result["date"] >= row.start.normalize()) & (result["date"] <= row.end.normalize())
        result.loc[mask, "actual_label"] = 1
    return result


def add_past_deviations(days: pd.DataFrame, warmup_days: int = 14) -> pd.DataFrame:
    """Add personal expanding median/MAD deviations using past rows only."""
    result = days.sort_values(["user", "date"]).copy()
    result["past_history_days"] = result.groupby("user").cumcount()
    for feature in DAILY_FEATURES:
        grouped = result.groupby("user")[feature]
        median = grouped.transform(lambda series: series.shift(1).expanding().median())
        mad = grouped.transform(lambda series: (series.shift(1) - series.shift(1).expanding().median()).abs().expanding().median())
        scale = (1.4826 * mad).where(mad > 0, median.abs().clip(lower=1))
        result[f"{feature}_personal_dev"] = ((result[feature] - median).abs() / scale).replace([np.inf, -np.inf], np.nan).fillna(0)
    deviation_columns = [f"{feature}_personal_dev" for feature in DAILY_FEATURES]
    result["personal_deviation"] = result[deviation_columns].mean(axis=1)
    result.loc[result["past_history_days"] < warmup_days, "personal_deviation"] = 0.0
    role_medians = result.groupby(["date", "role"], dropna=False)[DAILY_FEATURES].transform("median") if "role" in result else result[DAILY_FEATURES].median()
    if isinstance(role_medians, pd.DataFrame):
        result["role_deviation"] = ((result[DAILY_FEATURES] - role_medians).abs() / role_medians.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan).fillna(0).mean(axis=1)
    else:
        result["role_deviation"] = 0.0
    return result


def _raw_ensemble(train: pd.DataFrame, test: pd.DataFrame, features: list[str], contamination: float, nu: float, ocsvm_limit: int | None = None) -> tuple[np.ndarray, np.ndarray]:
    ocsvm_limit = ocsvm_limit or int(os.environ.get("INSIGHTER_CERT_OCSVM_LIMIT", DEFAULT_OCSVM_LIMIT))
    train_matrix = train[features].fillna(0).astype(float)
    test_matrix = test[features].fillna(0).astype(float)
    scaler = StandardScaler().fit(train_matrix)
    train_values = scaler.transform(train_matrix)
    test_values = scaler.transform(test_matrix)
    rng = np.random.default_rng(42)
    fit_values = train_values if len(train_values) <= ocsvm_limit else train_values[rng.choice(len(train_values), ocsvm_limit, replace=False)]
    if_model = IsolationForest(contamination=contamination, random_state=42, n_estimators=80).fit(fit_values)
    ocsvm = OneClassSVM(kernel="rbf", nu=nu, gamma="auto").fit(fit_values)
    if_reference = -if_model.decision_function(fit_values)
    svm_reference = -ocsvm.decision_function(fit_values)
    if_raw = -if_model.decision_function(test_values)
    svm_raw = -ocsvm.decision_function(test_values)
    return if_raw, svm_raw


def _rank_calibrate(values: np.ndarray, reference: np.ndarray, clipped: bool) -> np.ndarray:
    reference = np.sort(np.asarray(reference, dtype=float))
    ranks = np.searchsorted(reference, values, side="right") / max(1, len(reference)) * 100
    return np.clip(ranks, 0, 100) if clipped else ranks


def score_users_unsupervised(train: pd.DataFrame, test: pd.DataFrame, features: list[str], contamination: float, nu: float, clipped: bool = False, aggregate: str = "mean") -> pd.DataFrame:
    scored = score_rows_unsupervised(train, test, features, contamination, nu, clipped)
    if aggregate == "max":
        return scored.groupby("user", as_index=False)["score"].max()
    if aggregate == "top3":
        return scored.sort_values(["user", "score"], ascending=[True, False]).groupby("user", as_index=False).head(3).groupby("user", as_index=False)["score"].mean()
    return scored.groupby("user", as_index=False)["score"].mean()


def score_rows_unsupervised(train: pd.DataFrame, test: pd.DataFrame, features: list[str], contamination: float, nu: float, clipped: bool = False) -> pd.DataFrame:
    if_raw, svm_raw = _raw_ensemble(train, test, features, contamination, nu)
    scored = test[["user"]].copy().reset_index(drop=True)
    raw_score = 0.6 * if_raw + 0.4 * svm_raw
    if clipped:
        train_if, train_svm = _raw_ensemble(train, train, features, contamination, nu)
        scored["score"] = 0.6 * _rank_calibrate(if_raw, train_if, True) + 0.4 * _rank_calibrate(svm_raw, train_svm, True)
    else:
        scored["score"] = raw_score
    scored["raw_score"] = raw_score
    return scored


def metrics_at_k(scores: pd.DataFrame, k: float) -> dict[str, float]:
    values = scores.sort_values("score", ascending=False).copy()
    count = max(1, int(np.ceil(len(values) * k)))
    predicted = np.zeros(len(values), dtype=int)
    predicted[:count] = 1
    labels = values["actual_label"].to_numpy(dtype=int)
    return {
        "precision": float(precision_score(labels, predicted, zero_division=0)),
        "recall": float(recall_score(labels, predicted, zero_division=0)),
    }


def evaluate_scores(scores: pd.DataFrame) -> dict[str, float]:
    labels = scores["actual_label"].astype(int)
    return {
        "roc_auc": float(roc_auc_score(labels, scores["score"])),
        "pr_auc": float(average_precision_score(labels, scores["score"])),
        "recall_at_5": metrics_at_k(scores, 0.05)["recall"],
        "precision_at_5": metrics_at_k(scores, 0.05)["precision"],
        "recall_at_10": metrics_at_k(scores, 0.10)["recall"],
    }


def add_labels_to_users(scores: pd.DataFrame, answers: pd.DataFrame) -> pd.DataFrame:
    insiders = set(answers["user"].astype(str))
    result = scores.copy()
    result["actual_label"] = result["user"].astype(str).isin(insiders).astype(int)
    return result


def budget_cutoff(scores: pd.DataFrame, budget: float = 0.05) -> tuple[float, pd.DataFrame]:
    if not 0 < budget <= 1:
        raise ValueError("budget must be between 0 and 1")
    result = scores.copy()
    if "role" in result:
        result["role_rank"] = result.groupby("role")["score"].rank(pct=True, method="average")
        rank_values = result["role_rank"]
    else:
        rank_values = result["score"].rank(pct=True, method="average")
    cutoff = float(rank_values.quantile(1 - budget))
    result["flagged"] = rank_values >= cutoff
    return cutoff, result


def repeated_evaluation(features: pd.DataFrame, answers: pd.DataFrame, feature_columns: list[str], repeats: int = 5, folds: int = 3, clipped: bool = False, aggregate: str = "mean") -> pd.DataFrame:
    users = features.groupby("user", as_index=False)["actual_label"].max()
    rows = []
    for repeat in range(repeats):
        splitter = StratifiedKFold(n_splits=folds, shuffle=True, random_state=42 + repeat)
        for fold, (train_indices, test_indices) in enumerate(splitter.split(users["user"], users["actual_label"])):
            train_users = set(users.iloc[train_indices]["user"])
            test_users = set(users.iloc[test_indices]["user"])
            train = features[features["user"].isin(train_users)]
            test = features[features["user"].isin(test_users)]
            row_scores = score_rows_unsupervised(train, test, feature_columns, 0.08, 0.08, clipped=clipped)
            row_scores["actual_label"] = test["actual_label"].to_numpy(dtype=int)
            row_metrics = evaluate_scores(row_scores)
            if aggregate == "max":
                scores = row_scores.groupby("user", as_index=False)["score"].max()
            elif aggregate == "top3":
                scores = row_scores.sort_values(["user", "score"], ascending=[True, False]).groupby("user", as_index=False).head(3).groupby("user", as_index=False)["score"].mean()
            else:
                scores = row_scores.groupby("user", as_index=False)["score"].mean()
            scores = add_labels_to_users(scores, answers)
            user_metrics = evaluate_scores(scores)
            rows.append(
                {f"user_{name}": value for name, value in user_metrics.items()}
                | {f"day_{name}": value for name, value in row_metrics.items()}
                | {"repeat": repeat, "fold": fold}
            )
    return pd.DataFrame(rows)


def choose_aggregator(features: pd.DataFrame, answers: pd.DataFrame, feature_columns: list[str], seed: int = 42) -> str:
    """Choose the user aggregator on an inner tune split only."""
    users = features.groupby("user", as_index=False)["actual_label"].max()
    splitter = StratifiedShuffleSplit(n_splits=1, test_size=0.25, random_state=seed)
    train_indices, validation_indices = next(splitter.split(users["user"], users["actual_label"]))
    train_users = set(users.iloc[train_indices]["user"])
    validation_users = set(users.iloc[validation_indices]["user"])
    train = features[features["user"].isin(train_users)]
    validation = features[features["user"].isin(validation_users)]
    best_name, best_pr = "mean", -1.0
    for name in ("mean", "top3", "max"):
        scores = score_users_unsupervised(train, validation, feature_columns, 0.08, 0.08, aggregate=name)
        scores = add_labels_to_users(scores, answers)
        pr = evaluate_scores(scores)["pr_auc"]
        if pr > best_pr:
            best_name, best_pr = name, pr
    return best_name


def summarize(metrics: pd.DataFrame) -> pd.Series:
    names = [
        "user_roc_auc", "user_pr_auc", "user_recall_at_5", "user_precision_at_5", "user_recall_at_10",
        "day_roc_auc", "day_pr_auc", "day_recall_at_5", "day_precision_at_5", "day_recall_at_10",
    ]
    return pd.Series({name: f"{metrics[name].mean():.3f} +/- {metrics[name].std(ddof=1):.3f}" for name in names})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cert-dir", type=Path, required=True)
    parser.add_argument("--answer-dir", type=Path, required=True)
    parser.add_argument("--skip-http", action="store_true")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--folds", type=int, default=3)
    parser.add_argument("--aggregate", choices=("mean", "top3", "max", "tune"), default="tune")
    parser.add_argument("--stage", choices=("base", "deviations", "all"), default="all")
    args = parser.parse_args()
    answers = load_ground_truth(args.answer_dir)
    print("Building user-day table in chunks...")
    days = build_user_day_table(args.cert_dir, include_http=not args.skip_http)
    days = apply_insider_day_labels(days, answers)
    roles = _load_roles(args.cert_dir / "LDAP")
    days["role"] = days["user"].map(roles).fillna("unknown")
    days = add_past_deviations(days)
    ocsvm_limit = int(os.environ.get("INSIGHTER_CERT_OCSVM_LIMIT", DEFAULT_OCSVM_LIMIT))
    print(f"User-day rows: {len(days):,}; users: {days['user'].nunique():,}; OC-SVM cap: {ocsvm_limit:,} rows")
    user_labels = days.groupby("user", as_index=False)["actual_label"].max()
    daily = days.merge(user_labels, on="user", suffixes=("", "_user"))
    daily["actual_label"] = daily["actual_label_user"]
    daily = daily.drop(columns=["actual_label_user"])
    selected_aggregate = args.aggregate
    if selected_aggregate == "tune":
        selected_aggregate = choose_aggregator(daily, answers, DAILY_FEATURES)
    print(f"Selected user-day aggregator on tune split: {selected_aggregate}")
    deviation_features = ["personal_deviation", "role_deviation"]
    stages = [(DAILY_FEATURES, "user-day")]
    if args.stage in {"deviations", "all"}:
        stages.append((DAILY_FEATURES + deviation_features, "user-day + deviations"))
    if not args.skip_http and args.stage == "all":
        stages.append((DAILY_FEATURES + deviation_features + HTTP_FEATURES, "user-day + deviations + HTTP"))
    for columns, title in stages:
        available = [column for column in columns if column in daily]
        metrics = repeated_evaluation(daily, answers, available, args.repeats, args.folds, aggregate=selected_aggregate)
        print(f"{title}: {summarize(metrics).to_dict()}")
    del days, daily
    gc.collect()


if __name__ == "__main__":
    main()