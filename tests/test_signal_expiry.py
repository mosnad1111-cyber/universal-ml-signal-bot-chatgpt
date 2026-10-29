import unittest
import pandas as pd

from backtest import GoldBacktest


def bars(rows, start="2026-10-14T10:00:00Z", freq="5min"):
    idx = pd.date_range(start, periods=len(rows), freq=freq)
    return pd.DataFrame(rows, index=idx)


class NoTimeExpiryRegressionTests(unittest.TestCase):
    def setUp(self):
        self.bt = GoldBacktest()
        self.setup = {"direction": "BUY", "entry": 100.0, "sl": 90.0, "tp": 120.0}

    def test_unfilled_setup_remains_pending_until_entry_is_touched(self):
        df = bars(
            [{"High": 99, "Low": 95, "Close": 98} for _ in range(8)]
            + [{"High": 101, "Low": 99, "Close": 100}]
            + [{"High": 121, "Low": 100, "Close": 120}]
        )
        result, end_j, act_j = self.bt._resolve(df, 0, self.setup)
        self.assertEqual(result, "TP")
        self.assertEqual(end_j, 9)
        self.assertEqual(act_j, 8)

    def test_activated_setup_does_not_timeout_after_48_bars(self):
        rows = [{"High": 99, "Low": 95, "Close": 98} for _ in range(52)]
        rows[1] = {"High": 101, "Low": 99, "Close": 100}
        rows[50] = {"High": 121, "Low": 100, "Close": 120}
        df = bars(rows)
        result, end_j, act_j = self.bt._resolve(df, 0, self.setup)
        self.assertEqual(result, "TP")
        self.assertEqual(end_j, 50)
        self.assertEqual(act_j, 1)

    def test_stop_is_counted_conservatively_when_both_levels_touch(self):
        df = bars([
            {"High": 99, "Low": 95, "Close": 98},
            {"High": 121, "Low": 89, "Close": 100},
        ])
        result, end_j, act_j = self.bt._resolve(df, 0, self.setup)
        self.assertEqual(result, "SL")
        self.assertEqual(end_j, 1)
        self.assertEqual(act_j, 1)


if __name__ == "__main__":
    unittest.main()
