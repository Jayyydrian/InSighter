"""Evaluate CERT-derived features with InSighter's existing model pipeline."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
from sklearn.metrics import average_precision_score, fbeta_score, roc_auc_score

from cert_adapter import _normal, build_feature_table, load_ground_truth, resolve_cert_paths
from model import FEATURES, _run_ensemble

CERT_FEATURES = FEATURES + ["exfil_domain_hits", "job_site_hits"]


def risk_level(score: float, cutoffs: dict[str, float] | None = None) -> str:
    """Map a score to a risk level using fixed or calibrated cutoffs."""
    cutoffs = cutoffs or {"medium": 30.0, "high": 45.0, "critical": 70.0}
    if score >= cutoffs["critical"]:
        return "CRITICAL"
    if score >= cutoffs["high"]:
        return "HIGH"
    if score >= cutoffs["medium"]:
        return "MEDIUM"
    return "LOW"


def split_users(scores: pd.DataFrame, tune_ratio: float = 0.7, random_state: int = 42) -> tuple[set[str], set[str]]:
    """Return deterministic, approximately stratified tune and test user sets."""
    if not 0 < tune_ratio < 1:
        raise ValueError("tune_ratio must be between 0 and 1")
    tune_users: set[str] = set()
    test_users: set[str] = set()
    labeled = scores.assign(user=scores["user"].astype(str))
    for label, group in labeled.groupby("actual_label"):
        users = group["user"].drop_duplicates().sample(frac=1, random_state=random_state + int(label) + 1).tolist()
        count = max(1, min(len(users) - 1, round(len(users) * tune_ratio))) if len(users) > 1 else len(users)
        tune_users.update(users[:count])
        test_users.update(users[count:])
    if not tune_users or not test_users:
        raise ValueError("tune/test split must contain users in both groups")
    return tune_users, test_users


def calibrated_cutoffs(tune_scores: pd.Series, insider_rate: float) -> dict[str, float]:
    """Set score cutoffs from tune-score quantiles, including the CERT base rate."""
    if tune_scores.empty or not 0 < insider_rate < 1:
        raise ValueError("calibration requires scores and an insider rate between 0 and 1")
    return {
        "medium": float(tune_scores.quantile(0.80)),
        "high": float(tune_scores.quantile(1.0 - insider_rate)),
        "critical": float(tune_scores.quantile(1.0 - insider_rate / 3.0)),
    }


def youden_threshold(scores: pd.DataFrame) -> tuple[float, float, float]:
    """Return the tune-only threshold maximizing Youden's J statistic."""
    if scores.empty or scores["actual_label"].nunique() < 2:
        raise ValueError("Youden calibration requires insider and non-insider tune users")
    labels = scores["actual_label"].astype(int).to_numpy()
    values = scores["final_score"].astype(float).to_numpy()
    candidates = sorted(set(values), reverse=True)
    best = None
    for threshold in candidates:
        predicted = values >= threshold
        true_positive = int((predicted & (labels == 1)).sum())
        false_positive = int((predicted & (labels == 0)).sum())
        positives = int((labels == 1).sum())
        negatives = int((labels == 0).sum())
        tpr = true_positive / positives if positives else 0.0
        fpr = false_positive / negatives if negatives else 0.0
        statistic = tpr - fpr
        candidate = (statistic, -threshold, threshold, tpr, fpr)
        if best is None or candidate > best:
            best = candidate
    assert best is not None
    return best[2], best[3], best[4]


def fbeta_threshold(scores: pd.DataFrame, beta: float = 0.5) -> tuple[float, float]:
    """Return the tune-only threshold maximizing precision-weighted F-beta."""
    if beta <= 0:
        raise ValueError("F-beta beta must be greater than zero")
    if scores.empty or scores["actual_label"].nunique() < 2:
        raise ValueError("F-beta calibration requires insider and non-insider tune users")
    values = scores["final_score"].astype(float).to_numpy()
    labels = scores["actual_label"].astype(int).to_numpy()
    best = None
    for threshold in sorted(set(values), reverse=True):
        predicted = (values >= threshold).astype(int)
        metric = float(fbeta_score(labels, predicted, beta=beta, zero_division=0))
        candidate = (metric, threshold)
        if best is None or candidate > best:
            best = candidate
    assert best is not None
    threshold = best[1]
    return threshold, best[0]


def cutoffs_for_method(
    tune_scores: pd.DataFrame,
    insider_rate: float,
    method: str,
    fbeta_beta: float = 0.5,
) -> tuple[dict[str, float], dict[str, float]]:
    """Build risk cutoffs and calibration details from tune scores only."""
    if method == "top-rate":
        return calibrated_cutoffs(tune_scores["final_score"], insider_rate), {}
    if method != "youden":
        if method != "fbeta":
            raise ValueError("cutoff method must be 'top-rate', 'youden', or 'fbeta'")
        threshold, score = fbeta_threshold(tune_scores, fbeta_beta)
        quantile_cutoffs = calibrated_cutoffs(tune_scores["final_score"], insider_rate)
        cutoffs = {
            "medium": min(quantile_cutoffs["medium"], threshold),
            "high": threshold,
            "critical": max(quantile_cutoffs["critical"], threshold),
        }
        return cutoffs, {"threshold": threshold, "fbeta": score, "beta": fbeta_beta}
    threshold, tpr, fpr = youden_threshold(tune_scores)
    quantile_cutoffs = calibrated_cutoffs(tune_scores["final_score"], insider_rate)
    cutoffs = {
        "medium": min(quantile_cutoffs["medium"], threshold),
        "high": threshold,
        "critical": max(quantile_cutoffs["critical"], threshold),
    }
    return cutoffs, {"threshold": threshold, "tpr": tpr, "fpr": fpr}
def auc_metrics(scores: pd.DataFrame) -> dict[str, float]:
    """Calculate threshold-independent ranking metrics from raw final scores."""
    labels = scores["actual_label"].astype(int)
    if labels.nunique() < 2:
        raise ValueError("ROC-AUC and PR-AUC require both insider and non-insider users")
    return {
        "roc_auc": float(roc_auc_score(labels, scores["final_score"])),
        "pr_auc": float(average_precision_score(labels, scores["final_score"])),
    }


def _label_scores(scored: pd.DataFrame, insiders: set[str]) -> pd.DataFrame:
    scores = scored.groupby("user", as_index=False)["final_score"].mean()
    scores["user"] = scores["user"].astype(str)
    scores["actual_label"] = scores["user"].isin(insiders).astype(int)
    scores["actual_risk"] = scores["actual_label"].map({0: "LOW", 1: "CRITICAL"})
    return scores


def _print_metrics(scores: pd.DataFrame, cutoffs: dict[str, float], title: str) -> dict[str, float]:
    scores = scores.copy()
    scores["predicted_risk"] = scores["final_score"].map(lambda score: risk_level(score, cutoffs))
    predicted_positive = scores["predicted_risk"].isin({"HIGH", "CRITICAL"})
    actual_positive = scores["actual_label"].astype(bool)
    true_positive = int((predicted_positive & actual_positive).sum())
    false_positive = int((predicted_positive & ~actual_positive).sum())
    false_negative = int((~predicted_positive & actual_positive).sum())
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
    operational_flag_rate = float(predicted_positive.mean())
    metrics = auc_metrics(scores)
    print(title)
    print(f"Threshold-independent ROC-AUC: {metrics['roc_auc']:.3f}")
    print(f"Threshold-independent PR-AUC:  {metrics['pr_auc']:.3f}")
    print(f"HIGH+CRITICAL precision: {precision:.3f}")
    print(f"HIGH+CRITICAL recall:    {recall:.3f}")
    print(f"Operational flag rate:   {operational_flag_rate:.3f}")
    print("Four-tier confusion breakdown (actual x predicted):")
    matrix = pd.crosstab(scores["actual_risk"], scores["predicted_risk"], dropna=False)
    print(matrix.reindex(index=["LOW", "MEDIUM", "HIGH", "CRITICAL"], columns=["LOW", "MEDIUM", "HIGH", "CRITICAL"], fill_value=0).to_string())
    return {
        "roc_auc": metrics["roc_auc"],
        "pr_auc": metrics["pr_auc"],
        "precision": precision,
        "recall": recall,
        "operational_flag_rate": operational_flag_rate,
    }


def _audit_source_rows(cert_dir: Path, users: set[str], chunk_size: int = 100_000) -> dict[str, dict[str, float]]:
    """Reconstruct selected user aggregates directly from raw CERT CSV rows."""
    audit = {
        user: {
            "logon_rows": 0, "login_hours": [], "file_rows": 0,
            "file_media_hits": 0, "device_rows": 0, "device_connects": 0,
            "email_rows": 0, "email_bytes": 0.0,
        }
        for user in users
    }

    def column(frame: pd.DataFrame, names: tuple[str, ...], required: bool = True) -> str | None:
        available = {_normal(name): name for name in frame.columns}
        for name in names:
            if _normal(name) in available:
                return available[_normal(name)]
        if required:
            raise ValueError(f"Missing one of {names} in CERT source columns {list(frame.columns)}")
        return None

    def scan(path: Path, aliases: tuple[str, ...], handler) -> None:
        if not path.is_file():
            return
        header = pd.read_csv(path, nrows=0)
        user_column = column(header, ("user", "username", "user_id", "userid", "employee"))
        value_columns = handler("columns", header, user_column)
        usecols = [user_column] + [value for value in value_columns if value is not None]
        for chunk in pd.read_csv(path, usecols=usecols, chunksize=chunk_size, low_memory=False):
            handler("rows", chunk, user_column)

    def logon_handler(mode, frame, user_column):
        timestamp = column(frame, ("date", "timestamp", "datetime", "time")) if mode == "columns" else None
        if mode == "columns":
            return [timestamp]
        for username, value in zip(frame[user_column], frame[frame.columns[-1]]):
            user = str(username).strip()
            if user in audit and pd.notna(value):
                audit[user]["logon_rows"] += 1
                audit[user]["login_hours"].append(pd.to_datetime(value, errors="coerce").hour)
        return []

    def file_handler(mode, frame, user_column):
        if mode == "columns":
            return [column(frame, ("to_removable_media", "toremovablemedia"), False), column(frame, ("from_removable_media", "fromremovablemedia"), False)]
        media_columns = [value for value in frame.columns if value != user_column]
        for username, row in zip(frame[user_column], frame.to_dict(orient="records")):
            user = str(username).strip()
            if user in audit:
                audit[user]["file_rows"] += 1
                audit[user]["file_media_hits"] += int(any(str(row.get(value, "")).strip().lower() in {"1", "true", "yes"} for value in media_columns))
        return []

    def device_handler(mode, frame, user_column):
        activity = column(frame, ("activity", "action", "event")) if mode == "columns" else None
        if mode == "columns":
            return [activity]
        for username, value in zip(frame[user_column], frame[frame.columns[-1]]):
            user = str(username).strip()
            if user in audit:
                audit[user]["device_rows"] += 1
                audit[user]["device_connects"] += int(str(value).strip().lower() == "connect")
        return []

    def email_handler(mode, frame, user_column):
        size = column(frame, ("size", "attachment_size", "attachmentsize", "bytes")) if mode == "columns" else None
        if mode == "columns":
            return [size]
        for username, value in zip(frame[user_column], frame[frame.columns[-1]]):
            user = str(username).strip()
            if user in audit and pd.notna(value):
                audit[user]["email_rows"] += 1
                audit[user]["email_bytes"] += float(value)
        return []

    scan(cert_dir / "logon.csv", ("date",), logon_handler)
    scan(cert_dir / "file.csv", ("file",), file_handler)
    scan(cert_dir / "device.csv", ("device",), device_handler)
    scan(cert_dir / "email.csv", ("email",), email_handler)
    return audit


def _print_raw_feature_audit(cert_dir: Path, test_scores: pd.DataFrame, feature_table: pd.DataFrame) -> None:
    """Compare top outliers to population distributions and raw source rows."""
    numeric = [
        "login_hour", "files_accessed", "data_transferred_mb", "failed_logins",
        "exfil_domain_hits", "job_site_hits",
    ]
    available = [name for name in numeric if name in feature_table.columns]
    stats = {
        name: (float(feature_table[name].median()), float(feature_table[name].quantile(0.99)))
        for name in available
    }
    top = test_scores[test_scores["actual_label"] == 0].nlargest(20, "final_score").copy()
    top["user"] = top["user"].astype(str)
    raw = _audit_source_rows(cert_dir, set(top["user"]))
    print("Raw feature audit for top 20 non-insiders:")
    for row in top.itertuples(index=False):
        feature_row = feature_table[feature_table["user"].astype(str) == row.user].iloc[0]
        values = ", ".join(f"{name}={feature_row[name]:.3f}" for name in available)
        comparisons = "; ".join(
            f"{name}: median={stats[name][0]:.3f}, p99={stats[name][1]:.3f}"
            for name in available
        )
        source = raw[row.user]
        reconstructed_files = source["file_rows"]
        reconstructed_mb = source["email_bytes"] / (1024 * 1024)
        reconstructed_media = source["file_media_hits"] + source["device_connects"]
        matches = (
            int(feature_row["files_accessed"]) == int(reconstructed_files)
            and abs(float(feature_row["data_transferred_mb"]) - reconstructed_mb) < 0.001
            and int(feature_row["removable_media_events"]) == int(reconstructed_media)
        )
        extreme = any(
            name != "failed_logins" and stats[name][1] > 0 and float(feature_row[name]) > 10 * stats[name][1]
            for name in available
        )
        finding = (
            "aggregation appears to have produced an inflated value -- likely cause: "
            "raw reconstruction does not match feature aggregate"
            if not matches else
            "raw data confirms genuinely high activity" if extreme else
            "raw data confirms aggregate; no implausible feature inflation detected"
        )
        print(f"  {row.user} | final_score={row.final_score:.3f} | {values}")
        print(f"    population: {comparisons}")
        print(
            f"    raw rows: file={int(source['file_rows'])}, device={int(source['device_rows'])}, "
            f"email={int(source['email_rows'])}; reconstructed email_mb={reconstructed_mb:.3f}; "
            f"failed_logins={int(feature_row['failed_logins'])}; {finding}"
        )


def _print_calibration_diagnostics(
    test_scores: pd.DataFrame,
    tune_scores: pd.DataFrame,
    cutoffs: dict[str, float],
    insider_rate: float,
    feature_table: pd.DataFrame,
    tune_users: set[str],
) -> None:
    """Explain held-out zero recall without changing the reported evaluation."""
    test_scores = test_scores.copy()
    role_lookup = feature_table[["user", "role"]].copy()
    role_lookup["user"] = role_lookup["user"].astype(str)
    test_scores = test_scores.merge(role_lookup, on="user", how="left")
    test_scores["risk_level"] = test_scores["final_score"].map(
        lambda score: risk_level(score, cutoffs)
    )
    insiders = test_scores[test_scores["actual_label"] == 1].copy()
    print("Calibration diagnostics (held-out test split):")
    print("Known insiders in test split:")
    if insiders.empty:
        print("  none")
    else:
        print("  user | final_score | risk_level | gap_to_HIGH_cutoff")
        for row in insiders.sort_values("final_score", ascending=False).itertuples(index=False):
            gap = row.final_score - cutoffs["high"]
            print(
                f"  {row.user} | score {row.final_score:.3f} | {row.risk_level} | "
                f"HIGH cutoff {cutoffs['high']:.3f}, gap {gap:+.3f}"
            )

    non_insiders = test_scores[test_scores["actual_label"] == 0]
    print("Top 20 non-insiders in held-out test split:")
    print("  user | final_score | role")
    for row in non_insiders.sort_values("final_score", ascending=False).head(20).itertuples(index=False):
        print(f"  {row.user} | {row.final_score:.3f} | {row.role}")

    top_count = max(1, int(len(test_scores) * 0.10))
    top_users = set(test_scores.nlargest(top_count, "final_score")["user"])
    normalized_roles = test_scores["role"].fillna("unknown").astype(str).str.lower()
    privileged_mask = normalized_roles.isin({"itadmin", "administrator", "privileged", "superuser"})
    privileged_users = set(test_scores.loc[privileged_mask, "user"])
    privileged_top_fraction = (
        len(privileged_users & top_users) / len(privileged_users) if privileged_users else 0.0
    )
    print(
        f"Privileged-role top-10% concentration: {len(privileged_users & top_users)}/"
        f"{len(privileged_users)} = {privileged_top_fraction:.3f}; overall expected = 0.100"
    )
    if privileged_users:
        role_counts = test_scores.loc[privileged_mask, "role"].value_counts().to_dict()
        print(f"  privileged roles detected: {role_counts}")
    else:
        print("  privileged roles detected: none")

    tune_features = feature_table[feature_table["user"].astype(str).isin(tune_users)].copy()
    tune_roles = tune_features.groupby("role").agg(
        users=("user", "nunique"), rows=("user", "size")
    )
    print("CERT role-group OC-SVM eligibility for the selected tune split:")
    for role, values in tune_roles.sort_index().iterrows():
        eligible = role != "unknown" and values["users"] >= 2 and values["rows"] >= 20
        print(
            f"  {role}: users={int(values['users'])}, rows={int(values['rows'])}, "
            f"role-specific model={'eligible' if eligible else 'fallback to global'}"
        )
    itadmin_roles = [role for role in tune_roles.index if str(role).lower() == "itadmin"]
    if itadmin_roles:
        role = itadmin_roles[0]
        values = tune_roles.loc[role]
        eligible = values["users"] >= 2 and values["rows"] >= 20
        print(
            f"ITAdmin role match: exact CERT role present; users={int(values['users'])}, "
            f"rows={int(values['rows'])}; {'role-specific OC-SVM eligible' if eligible else 'global fallback'}"
        )
    else:
        print("ITAdmin role match: no exact ITAdmin role in the tune split; global fallback")

    print("Test score distribution by class:")
    for label, name in ((1, "insiders"), (0, "non-insiders")):
        values = test_scores.loc[test_scores["actual_label"] == label, "final_score"]
        if values.empty:
            print(f"  {name}: no users")
        else:
            print(
                f"  {name}: mean={values.mean():.3f}, median={values.median():.3f}, "
                f"max={values.max():.3f}"
            )

    test_percentiles = test_scores["final_score"].quantile([0.50, 0.75, 0.90, 0.93, 0.95, 0.99])
    tune_percentiles = tune_scores["final_score"].quantile([0.50, 0.75, 0.90, 0.93, 0.95, 0.99])
    print("Tune cutoffs and score percentiles:")
    print(
        f"  calibrated cutoffs: MEDIUM={cutoffs['medium']:.3f}, "
        f"HIGH={cutoffs['high']:.3f}, CRITICAL={cutoffs['critical']:.3f}"
    )
    print("  percentile | tune scores | test scores")
    for percentile in test_percentiles.index:
        print(
            f"  {percentile:.0%} | {tune_percentiles[percentile]:.3f} | "
            f"{test_percentiles[percentile]:.3f}"
        )

    test_top_rate_cutoff = float(test_scores["final_score"].quantile(1.0 - insider_rate))
    diagnostic_recall = float(
        ((test_scores["actual_label"] == 1) & (test_scores["final_score"] >= test_top_rate_cutoff)).sum()
        / max(1, (test_scores["actual_label"] == 1).sum())
    )
    print(
        "Diagnostic-only test-top-rate recall (data leakage; not a valid evaluation): "
        f"{diagnostic_recall:.3f} using test cutoff {test_top_rate_cutoff:.3f}"
    )


def score_cert(
    cert_dir: Path | str,
    answer_dir: Path | str,
    ldap_dir: Path | str | None = None,
    ldap_month: str | None = None,
    tune_split: float = 0.7,
    calibrate: bool = True,
    random_state: int = 42,
    skip_http_signal: bool = False,
    label: str | None = None,
    cutoff_method: str = "top-rate",
    fbeta_beta: float = 0.5,
) -> pd.DataFrame:
    """Evaluate fixed thresholds or tune hyperparameters/cutoffs on a held-out split."""
    features = build_feature_table(
        cert_dir, ldap_dir, ldap_month=ldap_month, include_http_signal=not skip_http_signal
    )
    answers = load_ground_truth(answer_dir)
    scenario_text = answers["scenario"].astype(str).str.lower()
    insiders = set(answers.loc[scenario_text.str.contains("insider|malicious|threat"), "user"].astype(str))
    if not insiders:
        insiders = set(answers["user"].astype(str))
    split_frame = pd.DataFrame({"user": features["user"].astype(str).unique()})
    split_frame["actual_label"] = split_frame["user"].isin(insiders).astype(int)
    if not calibrate:
        all_scores = _label_scores(_run_ensemble(features.copy(), feature_list=CERT_FEATURES), insiders)
        metrics = _print_metrics(
            all_scores, {"medium": 30.0, "high": 45.0, "critical": 70.0},
            label or "Fixed-cutoff evaluation",
        )
        all_scores.attrs["evaluation_metrics"] = metrics
        return all_scores

    tune_users, test_users = split_users(split_frame, tune_split, random_state)
    tune_features = features[features["user"].astype(str).isin(tune_users)].copy()
    test_features = features[features["user"].astype(str).isin(test_users)].copy()
    grid = [(contamination, nu) for contamination in (0.05, 0.08, 0.12) for nu in (0.05, 0.08, 0.12)]
    best = None
    for contamination, nu in grid:
        candidate = _label_scores(
            _run_ensemble(tune_features.copy(), contamination, nu, feature_list=CERT_FEATURES), insiders
        )
        metric = auc_metrics(candidate)["pr_auc"]
        if best is None or metric > best[0]:
            best = (metric, contamination, nu, candidate)
    _, contamination, nu, tune_scores = best
    cutoffs, calibration_details = cutoffs_for_method(
        tune_scores, float(tune_scores["actual_label"].mean()), cutoff_method
        , fbeta_beta
    )
    test_scores = _label_scores(
        _run_ensemble(
            test_features.copy(), contamination, nu, fit_df=tune_features, feature_list=CERT_FEATURES
        ), insiders
    )
    print(f"Tune users: {len(tune_users)}; test users: {len(test_users)}")
    print(f"Chosen Isolation Forest contamination: {contamination:.2f}")
    print(f"Chosen OC-SVM nu: {nu:.2f}")
    print(f"Cutoff method: {cutoff_method}")
    if calibration_details:
        if cutoff_method == "youden":
            print(
                f"Youden tune threshold: {calibration_details['threshold']:.3f}; "
                f"tune TPR={calibration_details['tpr']:.3f}; "
                f"tune FPR={calibration_details['fpr']:.3f}"
            )
        else:
            print(
                f"F-beta tune threshold: {calibration_details['threshold']:.3f}; "
                f"beta={calibration_details['beta']:.3f}; "
                f"tune F-beta={calibration_details['fbeta']:.3f}"
            )
    print(f"Calibrated cutoffs: medium={cutoffs['medium']:.3f}, high={cutoffs['high']:.3f}, critical={cutoffs['critical']:.3f}")
    _print_calibration_diagnostics(
        test_scores, tune_scores, cutoffs, float(tune_scores["actual_label"].mean()),
        features, tune_users,
    )
    _print_raw_feature_audit(Path(cert_dir), test_scores, features)
    metrics = _print_metrics(test_scores, cutoffs, label or "Final held-out test evaluation")
    test_scores["predicted_risk"] = test_scores["final_score"].map(lambda score: risk_level(score, cutoffs))
    test_scores.attrs["evaluation_metrics"] = metrics
    return test_scores


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cert-dir", type=Path)
    parser.add_argument("--answer-dir", type=Path)
    parser.add_argument("--ldap-dir", type=Path)
    parser.add_argument("--ldap-month")
    parser.add_argument("--tune-split", type=float, default=0.7)
    parser.add_argument("--no-calibration", action="store_true")
    parser.add_argument("--skip-http-signal", action="store_true")
    parser.add_argument("--cutoff-method", choices=("top-rate", "youden", "fbeta"), default="top-rate")
    parser.add_argument("--fbeta-beta", type=float, default=0.5)
    args = parser.parse_args()
    try:
        cert_dir, answer_dir = resolve_cert_paths(args.cert_dir, args.answer_dir)
    except ValueError as exc:
        parser.error(str(exc))
    methods = ["top-rate", "youden", "fbeta"]
    comparisons = {}
    for method in methods:
        with_signal = score_cert(
            cert_dir, answer_dir, args.ldap_dir or cert_dir / "LDAP", args.ldap_month,
            tune_split=args.tune_split, calibrate=not args.no_calibration,
            label=f"With browsing signal ({method})", cutoff_method=method, fbeta_beta=args.fbeta_beta,
        )
        without_signal = score_cert(
            cert_dir, answer_dir, args.ldap_dir or cert_dir / "LDAP", args.ldap_month,
            tune_split=args.tune_split, calibrate=not args.no_calibration,
            skip_http_signal=True, label=f"Without browsing signal ({method})", cutoff_method=method, fbeta_beta=args.fbeta_beta,
        )
        comparisons[method] = {
            "with": with_signal.attrs["evaluation_metrics"],
            "without": without_signal.attrs["evaluation_metrics"],
        }
    print("Cutoff-method comparison (held-out test metrics, with browsing signal):")
    print("                     ROC-AUC   PR-AUC   Precision   Recall   Flag rate")
    for method in methods:
        metrics = comparisons[method]["with"]
        print(f"{method:>20} {metrics['roc_auc']:.3f}      {metrics['pr_auc']:.3f}      {metrics['precision']:.3f}       {metrics['recall']:.3f}      {metrics['operational_flag_rate']:.3f}")


if __name__ == "__main__":
    main()