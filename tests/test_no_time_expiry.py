import unittest
import pandas as pd

from backtest import GoldBacktest


class NoTimeExpiryTests(unittest.TestCase):
    def test_waiting_signal_can_activate_after_old_pending_ttl(self):
        rows = [{"High": 101.0, "Low": 99.0, "Close": 100.0} for _ in range(15)]
        rows[13] = {"High": 101.0, "Low": 100.0, "Close": 100.5}
        rows[14] = {"High": 104.0, "Low": 101.0, "Close": 103.0}
        df = pd.DataFrame(rows)
        setup = {"direction": "BUY", "entry": 100.0, "sl": 98.0, "tp": 103.0}
        result, end_idx, activation_idx = GoldBacktest()._resolve(df, 1, setup)
        self.assertEqual(result, "TP")
        self.assertEqual(activation_idx, 13)
        self.assertEqual(end_idx, 14)

    def test_active_trade_does_not_timeout_after_48_bars(self):
        rows = [{"High": 101.0, "Low": 99.0, "Close": 100.0} for _ in range(52)]
        rows[1] = {"High": 101.0, "Low": 100.0, "Close": 100.5}
        rows[50] = {"High": 104.0, "Low": 101.0, "Close": 103.0}
        df = pd.DataFrame(rows)
        setup = {"direction": "BUY", "entry": 100.0, "sl": 98.0, "tp": 103.0}
        result, end_idx, activation_idx = GoldBacktest()._resolve(df, 1, setup)
        self.assertEqual(result, "TP")
        self.assertEqual(activation_idx, 1)
        self.assertEqual(end_idx, 50)

    def test_unresolved_trade_stays_unresolved_at_end_of_history(self):
        rows = [{"High": 101.0, "Low": 99.0, "Close": 100.0} for _ in range(60)]
        rows[1] = {"High": 101.0, "Low": 100.0, "Close": 100.5}
        df = pd.DataFrame(rows)
        setup = {"direction": "BUY", "entry": 100.0, "sl": 98.0, "tp": 103.0}
        result, end_idx, activation_idx = GoldBacktest()._resolve(df, 1, setup)
        self.assertIsNone(result)
        self.assertIsNone(end_idx)
        self.assertEqual(activation_idx, 1)


if __name__ == "__main__":
    unittest.main()
