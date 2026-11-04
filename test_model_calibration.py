import unittest

import numpy as np
import pandas as pd

from model import (
    FEATURES,
    _prepare_model_features,
    _scale_0_100,
    _scoreable_observations,
)


class ModelCalibrationTest(unittest.TestCase):
    def test_scores_use_reference_distribution_instead_of_scored_batch(self):
        reference = np.array([0.0, 1.0, 2.0, 3.0, 4.0, 5.0])
        first_batch = _scale_0_100(np.array([2.0, 4.0]), reference)
        expanded_batch = _scale_0_100(np.array([2.0, 4.0, 100.0]), reference)

        np.testing.assert_allclose(first_batch, expanded_batch[:2])

    def test_extreme_scores_are_clipped_to_contract(self):
        reference = np.arange(20, dtype=float)

        scores = _scale_0_100(np.array([-100.0, 10.0, 100.0]), reference)

        np.testing.assert_array_equal(scores[[0, 2]], np.array([0.0, 100.0]))
        self.assertGreater(scores[1], 0.0)
        self.assertLess(scores[1], 100.0)

    def test_model_preparation_rejects_missing_measurements(self):
        frame = pd.DataFrame([{feature: 1 for feature in FEATURES}])
        frame.loc[0, "files_accessed"] = np.nan

        with self.assertRaisesRegex(ValueError, "unavailable measurements"):
            _prepare_model_features(frame)

    def test_sensor_observation_with_unsupported_features_is_not_scoreable(self):
        frame = pd.DataFrame([{
            "login_hour": 10,
            "files_accessed": None,
            "data_transferred_mb": 1.5,
            "failed_logins": None,
            "off_hours_access": 0,
            "feature_status": (
                '{"login_hour":"supported","files_accessed":"unsupported",'
                '"data_transferred_mb":"machine_wide","failed_logins":"unsupported",'
                '"off_hours_access":"supported"}'
            ),
        }])

        self.assertTrue(_scoreable_observations(frame).empty)


if __name__ == "__main__":
    unittest.main()