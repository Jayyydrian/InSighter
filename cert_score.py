"""Evaluate CERT-derived features with InSighter's existing model pipeline."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from cert_adapter import build_feature_table, load_ground_truth
from model import _run_ensemble


def risk_level(score: float) -> str:
    """Map the existing score thresholds to LOW/MEDIUM/HIGH/CRITICAL."""
    if score >= 70:
        return "CRITICAL"
    if score >= 45:
        return "HIGH"
    if score >= 30:
        return "MEDIUM"
    return "LOW"


def score_cert(
    cert_dir: Path | str,
    answer_dir: Path | str,
    ldap_dir: Path | str | None = None,
    ldap_month: str | None = None,
) -> pd.DataFrame:
    """Run the unchanged ensemble and print binary and four-tier metrics."""
    features = build_feature_table(cert_dir, ldap_dir, ldap_month=ldap_month)
    scored_events = _run_ensemble(features.copy())
    scores = scored_events.groupby("user", as_index=False)["final_score"].mean()
    scores["predicted_risk"] = scores["final_score"].map(risk_level)

    answers = load_ground_truth(answer_dir)
    scenario_text = answers["scenario"].astype(str).str.lower()
    insiders = set(answers.loc[scenario_text.str.contains("insider|malicious|threat"), "user"].astype(str))
    if not insiders:
        insiders = set(answers["user"].astype(str))
    scores["actual_risk"] = scores["user"].astype(str).map(lambda user: "CRITICAL" if user in insiders else "LOW")

    predicted_positive = scores["predicted_risk"].isin({"HIGH", "CRITICAL"})
    actual_positive = scores["actual_risk"].isin({"HIGH", "CRITICAL"})
    true_positive = int((predicted_positive & actual_positive).sum())
    false_positive = int((predicted_positive & ~actual_positive).sum())
    false_negative = int((~predicted_positive & actual_positive).sum())
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
    print(f"HIGH+CRITICAL precision: {precision:.3f}")
    print(f"HIGH+CRITICAL recall:    {recall:.3f}")
    print("Four-tier confusion breakdown (actual x predicted):")
    matrix = pd.crosstab(scores["actual_risk"], scores["predicted_risk"], dropna=False)
    print(matrix.reindex(index=["LOW", "MEDIUM", "HIGH", "CRITICAL"], columns=["LOW", "MEDIUM", "HIGH", "CRITICAL"], fill_value=0).to_string())
    return scores


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cert-dir", type=Path, default=Path.home() / "cert_data" / "r4.2")
    parser.add_argument("--answer-dir", type=Path, default=Path.home() / "cert_data" / "answers" / "r4.2")
    parser.add_argument("--ldap-dir", type=Path)
    parser.add_argument("--ldap-month")
    args = parser.parse_args()
    score_cert(args.cert_dir, args.answer_dir, args.ldap_dir or args.cert_dir / "LDAP", args.ldap_month)


if __name__ == "__main__":
    main()
