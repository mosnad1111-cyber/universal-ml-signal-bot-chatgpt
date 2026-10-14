import unittest

import config


class ThresholdSelectionTests(unittest.TestCase):
    def test_selected_per_timeframe_thresholds(self):
        # These are provisional grid-search candidates for out-of-sample validation.
        self.assertEqual(config.thresholds_for("5m"), (60.0, 0.60))
        self.assertEqual(config.thresholds_for("15m"), (65.0, 0.45))
        self.assertEqual(config.thresholds_for("1h"), (60.0, 0.45))

    def test_threshold_function_is_single_source_of_truth(self):
        self.assertEqual(config.thresholds_for("5m"), (config.MIN_SCORE_5M, config.MIN_AI_PROB_5M))
        self.assertEqual(config.thresholds_for("15m"), (config.MIN_SCORE_15M, config.MIN_AI_PROB_15M))
        self.assertEqual(config.thresholds_for("1h"), (config.MIN_SCORE_1H, config.MIN_AI_PROB_1H))


if __name__ == "__main__":
    unittest.main()
