import unittest

import numpy as np

from model import _scale_0_100


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


if __name__ == "__main__":
    unittest.main()