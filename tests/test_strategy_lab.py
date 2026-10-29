import unittest

from strategy_lab import summarize_trades, split_completed_trades


class StrategyLabTests(unittest.TestCase):
    def test_cost_sensitivity_reduces_net_return(self):
        trades = [{"r": 2.0}, {"r": -1.0}, {"r": 2.0}, {"r": -1.0}]
        gross = summarize_trades(trades, 0.0)
        after_cost = summarize_trades(trades, 0.10)
        self.assertAlmostEqual(gross["net_r"], 2.0)
        self.assertAlmostEqual(after_cost["net_after_cost_r"], 1.6)
        self.assertLessEqual(after_cost["profit_factor_after_cost"], gross["profit_factor"])

    def test_calibration_excludes_trades_that_exit_after_split(self):
        trades = [
            {"time": "2026-01-01T00:00:00+00:00", "exit_time": "2026-01-02T00:00:00+00:00", "r": 2.0},
            {"time": "2026-01-03T00:00:00+00:00", "exit_time": "2026-01-04T00:00:00+00:00", "r": -1.0},
            {"time": "2026-01-01T00:00:00+00:00", "exit_time": "2026-01-01T12:00:00+00:00", "r": -1.0},
        ]
        midpoint = "2026-01-02T00:00:00+00:00"
        from pandas import Timestamp
        calibration, holdout = split_completed_trades(trades, Timestamp(midpoint))
        self.assertEqual(len(calibration), 1)
        self.assertEqual(len(holdout), 1)
        self.assertEqual(calibration[0]["r"], -1.0)
        self.assertEqual(holdout[0]["r"], -1.0)

    def test_empty_sample_is_explicit(self):
        result = summarize_trades([])
        self.assertEqual(result["trades"], 0)
        self.assertEqual(result["net_r"], 0.0)


if __name__ == "__main__":
    unittest.main()
