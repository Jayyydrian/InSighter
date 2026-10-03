import unittest

import numpy as np
import pandas as pd

from cert_experiment import (
    DAILY_FEATURES,
    add_past_deviations,
    apply_insider_day_labels,
    budget_cutoff,
    _rank_calibrate,
)


class CertExperimentTest(unittest.TestCase):
    def test_day_labels_respect_inclusive_insider_window(self):
        days = pd.DataFrame({
            "user": ["alice", "alice", "alice", "bob"],
            "date": ["2020-01-01", "2020-01-02", "2020-01-03", "2020-01-02"],
        })
        answers = pd.DataFrame({
            "user": ["alice"],
            "scenario": [1],
            "start": ["2020-01-02 12:00:00"],
            "end": ["2020-01-03 01:00:00"],
        })

        labeled = apply_insider_day_labels(days, answers)

        self.assertEqual(labeled["actual_label"].tolist(), [0, 1, 1, 0])

    def test_personal_deviation_does_not_use_current_or_future_rows(self):
        rows = []
        for day in range(16):
            row = {feature: 0.0 for feature in DAILY_FEATURES}
            row.update({"user": "alice", "date": f"2020-01-{day + 1:02d}"})
            row["files_accessed"] = 1.0 if day < 15 else 100.0
            rows.append(row)
        days = pd.DataFrame(rows)

        result = add_past_deviations(days)

        self.assertEqual(result.loc[result["date"] == "2020-01-15", "personal_deviation"].iloc[0], 0.0)
        self.assertGreater(result.loc[result["date"] == "2020-01-16", "personal_deviation"].iloc[0], 0.0)

    def test_budget_cutoff_flags_requested_fraction(self):
        scores = pd.DataFrame({
            "user": [f"u{i}" for i in range(20)],
            "role": ["staff"] * 20,
            "score": np.arange(20, dtype=float),
        })

        _, result = budget_cutoff(scores, 0.05)

        self.assertEqual(int(result["flagged"].sum()), 1)

    def test_unclipped_rank_calibration_preserves_more_distinct_values(self):
        values = np.array([0.0, 1.0, 2.0, 2.0, 100.0])
        reference = np.array([0.0, 1.0, 2.0, 100.0])

        clipped = _rank_calibrate(values, reference, clipped=True)
        unclipped = _rank_calibrate(values, reference, clipped=False)

        self.assertLessEqual(len(np.unique(clipped)), len(np.unique(unclipped)))


if __name__ == "__main__":
    unittest.main()