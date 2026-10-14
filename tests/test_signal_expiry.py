import unittest
from unittest.mock import patch

import pandas as pd

from backtest import GoldBacktest
from engine import Engine


def bars(rows, start="2026-10-14T10:00:00Z", freq="5min"):
    idx = pd.date_range(start, periods=len(rows), freq=freq)
    return pd.DataFrame(rows, index=idx)


class SignalExpiryTests(unittest.TestCase):
    def setUp(self):
        self.bt = GoldBacktest()
        self.setup = {
            "direction": "BUY",
            "entry": 100.0,
            "sl": 90.0,
            "tp": 120.0,
        }

    def test_unfilled_signal_expires_after_pending_bar_limit(self):
        df = bars([
            {"High": 106, "Low": 104, "Close": 105},
            {"High": 106, "Low": 104, "Close": 105},
            {"High": 106, "Low": 104, "Close": 105},
            {"High": 106, "Low": 104, "Close": 105},
            {"High": 106, "Low": 104, "Close": 105},
        ])
        with patch("backtest.SIGNAL_PENDING_TTL_BARS", 3), patch(
            "backtest.SIGNAL_MAX_LIFETIME_BARS", 5
        ):
            result, end_j, act_j = self.bt._resolve(df, 1, self.setup)
        self.assertEqual(result, "EXPIRED_UNFILLED")
        self.assertEqual(end_j, 3)
        self.assertIsNone(act_j)

    def test_activated_trade_times_out_at_close_after_max_lifetime(self):
        df = bars([
            {"High": 101, "Low": 99, "Close": 100},
            {"High": 102, "Low": 99, "Close": 101},
            {"High": 103, "Low": 98, "Close": 102},
            {"High": 104, "Low": 97, "Close": 103},
            {"High": 105, "Low": 96, "Close": 104},
        ])
        with patch("backtest.SIGNAL_PENDING_TTL_BARS", 2), patch(
            "backtest.SIGNAL_MAX_LIFETIME_BARS", 3
        ):
            result, end_j, act_j = self.bt._resolve(df, 1, self.setup)
        self.assertEqual(result, "TIMEOUT")
        self.assertEqual(end_j, 3)
        self.assertEqual(act_j, 1)

    def test_stop_is_counted_conservatively_when_both_levels_touch(self):
        df = bars([
            {"High": 101, "Low": 99, "Close": 100},
            {"High": 121, "Low": 89, "Close": 100},
        ])
        with patch("backtest.SIGNAL_PENDING_TTL_BARS", 2), patch(
            "backtest.SIGNAL_MAX_LIFETIME_BARS", 3
        ):
            result, end_j, act_j = self.bt._resolve(df, 1, self.setup)
        self.assertEqual(result, "SL")
        self.assertEqual(end_j, 1)
        self.assertEqual(act_j, 1)

    def test_live_bar_age_is_timezone_safe(self):
        df = bars([{"Close": 1}, {"Close": 1}, {"Close": 1}])
        self.assertEqual(
            Engine._bars_since_signal(df, "2026-10-14T10:00:00+00:00"), 2
        )


if __name__ == "__main__":
    unittest.main()
