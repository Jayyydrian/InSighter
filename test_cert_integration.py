import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from cert_adapter import build_feature_table, feature_table_to_events, load_ground_truth
from cert_score import CERT_FEATURES, auc_metrics, calibrated_cutoffs, fbeta_threshold, score_cert, split_users, youden_threshold
from model import FEATURES, _prepare_model_features


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


class CertIntegrationTest(unittest.TestCase):
    def test_default_cert_scoring_passes_cert_features_to_ensemble(self):
        feature_table = pd.DataFrame({
            "user": ["alice", "bob"],
            "login_hour": [9, 10],
            "files_accessed": [2, 3],
            "data_transferred_mb": [10.0, 20.0],
            "failed_logins": [0, 0],
            "off_hours_access": [0, 0],
            "role": ["staff", "staff"],
            "exfil_domain_hits": [2, 0],
            "job_site_hits": [5, 0],
        })
        answers = pd.DataFrame({
            "user": ["alice", "bob"],
            "scenario": ["insider", "normal"],
            "start": ["2025-01-01", "2025-01-01"],
            "end": ["2025-01-02", "2025-01-02"],
        })
        captured = {}

        def fake_run_ensemble(frame, *args, **kwargs):
            captured["feature_list"] = kwargs["feature_list"]
            captured["matrix"] = frame.reindex(columns=kwargs["feature_list"])
            result = frame.copy()
            result["final_score"] = [80.0, 10.0]
            return result

        with patch("cert_score.build_feature_table", return_value=feature_table), \
                patch("cert_score.load_ground_truth", return_value=answers), \
                patch("cert_score._run_ensemble", side_effect=fake_run_ensemble):
            score_cert(Path("external-cert"), Path("external-answers"), calibrate=False)

        self.assertEqual(captured["feature_list"], CERT_FEATURES)
        self.assertIn("exfil_domain_hits", captured["matrix"].columns)
        self.assertIn("job_site_hits", captured["matrix"].columns)

    def test_http_ablation_changes_adapter_but_not_current_model_matrix(self):
        features = pd.DataFrame({
            "user": ["alice", "bob"],
            "login_hour": [9, 10],
            "files_accessed": [2, 3],
            "data_transferred_mb": [10.0, 20.0],
            "failed_logins": [0, 0],
            "off_hours_access": [0, 0],
            "role": ["staff", "staff"],
            "exfil_domain_hits": [2, 0],
            "job_site_hits": [5, 0],
        })
        without_http = features.copy()
        without_http[["exfil_domain_hits", "job_site_hits"]] = 0
        self.assertFalse(features[["exfil_domain_hits", "job_site_hits"]].equals(
            without_http[["exfil_domain_hits", "job_site_hits"]]
        ))
        self.assertEqual(list(_prepare_model_features(features).columns), FEATURES)
        self.assertNotIn("exfil_domain_hits", FEATURES)
        self.assertNotIn("job_site_hits", FEATURES)
        self.assertEqual(CERT_FEATURES, FEATURES + ["exfil_domain_hits", "job_site_hits"])
        self.assertFalse(
            _prepare_model_features(features, CERT_FEATURES).equals(
                _prepare_model_features(without_http, CERT_FEATURES)
            )
        )

    def test_youden_threshold_selects_known_separator(self):
        scores = pd.DataFrame({
            "final_score": [1.0, 2.0, 8.0, 9.0],
            "actual_label": [0, 0, 1, 1],
        })
        threshold, tpr, fpr = youden_threshold(scores)
        self.assertEqual(threshold, 8.0)
        self.assertEqual(tpr, 1.0)
        self.assertEqual(fpr, 0.0)

    def test_fbeta_favors_precision_over_youden(self):
        scores = pd.DataFrame({
            "final_score": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
            "actual_label": [0, 0, 0, 1, 0, 1],
        })
        youden, _, _ = youden_threshold(scores)
        fbeta, _ = fbeta_threshold(scores, beta=0.5)
        youden_precision = 2 / 3
        fbeta_precision = 1 / 1
        youden_flag_rate = 3 / 6
        fbeta_flag_rate = 1 / 6
        self.assertGreater(fbeta_precision, youden_precision)
        self.assertGreater(fbeta, youden)
        self.assertLess(fbeta_flag_rate, youden_flag_rate)

    def test_log1p_dampens_skewed_feature_magnitude(self):
        features = pd.DataFrame({
            "login_hour": [9, 9],
            "files_accessed": [1, 1],
            "data_transferred_mb": [1.0, 1.0],
            "failed_logins": [0, 0],
            "off_hours_access": [0, 0],
        })
        raw_gap = features.loc[1, "files_accessed"] - features.loc[0, "files_accessed"]
        features.loc[0, "data_transferred_mb"] = 50.0
        features.loc[1, "data_transferred_mb"] = 12000.0
        transformed = _prepare_model_features(features)
        transformed_gap = transformed.loc[1, "data_transferred_mb"] - transformed.loc[0, "data_transferred_mb"]
        self.assertLess(transformed_gap, 12000.0 - 50.0)
        self.assertGreater(transformed_gap, 0.0)
    def test_split_stratifies_insiders(self):
        scores = pd.DataFrame({
            "user": [f"u{i}" for i in range(10)],
            "actual_label": [1, 1] + [0] * 8,
        })
        tune, test = split_users(scores, 0.7)
        self.assertEqual(len(tune & test), 0)
        self.assertEqual(len(tune | test), 10)
        self.assertEqual(sum(scores.set_index("user").loc[list(tune), "actual_label"]), 1)
        self.assertEqual(sum(scores.set_index("user").loc[list(test), "actual_label"]), 1)

    def test_calibrated_cutoffs_use_tune_distribution(self):
        cutoffs = calibrated_cutoffs(pd.Series([10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]), 0.2)
        self.assertAlmostEqual(cutoffs["medium"], 82.0)
        self.assertAlmostEqual(cutoffs["high"], 82.0)
        self.assertGreater(cutoffs["critical"], cutoffs["high"])

    def test_auc_metrics_known_ranking(self):
        scores = pd.DataFrame({
            "final_score": [0.1, 0.2, 0.8, 0.9],
            "actual_label": [0, 0, 1, 1],
        })
        metrics = auc_metrics(scores)
        self.assertAlmostEqual(metrics["roc_auc"], 1.0)
        self.assertAlmostEqual(metrics["pr_auc"], 1.0)

    def test_fixture_files_build_features_and_events(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ldap = root / "LDAP"
            ldap.mkdir()
            write_csv(root / "logon.csv", [
                {"user": "alice", "date": "2025-01-01 05:00:00"},
                {"user": "alice", "date": "2025-01-01 13:00:00"},
                {"user": "bob", "date": "2025-01-01 09:00:00"},
            ])
            write_csv(root / "file.csv", [
                {"user": "alice", "to_removable_media": "true"},
                {"user": "alice", "to_removable_media": "false"},
                {"user": "bob", "to_removable_media": "false"},
            ])
            write_csv(root / "device.csv", [
                {"user": "alice", "activity": "Connect"},
                {"user": "alice", "activity": "Disconnect"},
                {"user": "bob", "activity": "Connect"},
            ])
            write_csv(root / "email.csv", [
                {"user": "alice", "size": 1048576},
                {"user": "bob", "size": 524288},
            ])
            write_csv(root / "http.csv", [
                {"user": "alice", "url": "https://wikileaks.org/upload"},
                {"user": "alice", "url": "https://www.indeed.com/jobs"},
                {"user": "bob", "url": "https://example.org/home"},
            ])
            write_csv(ldap / "ldap-2025-01.csv", [
                {"user": "alice", "role": "analyst"},
                {"user": "bob", "role": "engineer"},
            ])

            features = build_feature_table(root, chunk_size=1)
            alice = features.set_index("user").loc["alice"]
            self.assertEqual(alice["login_hour"], 9.0)
            self.assertEqual(alice["files_accessed"], 2)
            self.assertEqual(alice["data_transferred_mb"], 1.0)
            self.assertEqual(alice["off_hours_access"], 1)
            self.assertEqual(alice["removable_media_events"], 2)
            self.assertEqual(alice["role"], "analyst")
            self.assertEqual(alice["exfil_domain_hits"], 1)
            self.assertEqual(alice["job_site_hits"], 1)
            self.assertEqual(features.set_index("user").loc["bob", "exfil_domain_hits"], 0)
            self.assertEqual(features.set_index("user").loc["bob", "job_site_hits"], 0)

            events = feature_table_to_events(features)
            self.assertEqual({event["user"] for event in events}, {"alice", "bob"})
            self.assertNotIn("removable_media_events", events[0])
            self.assertEqual(events[0].keys(), {
                "user", "login_hour", "files_accessed", "data_transferred_mb",
                "failed_logins", "off_hours_access", "role",
            })

    def test_missing_ground_truth_has_clear_error(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(FileNotFoundError, "No answer-key CSV found"):
                load_ground_truth(directory)


if __name__ == "__main__":
    unittest.main()