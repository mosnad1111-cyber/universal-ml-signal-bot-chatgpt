import unittest
from unittest.mock import patch

import pandas as pd

from backtest import GoldBacktest


class GridSnapshotTests(unittest.TestCase):
    def test_grid_prepares_data_once_and_reuses_same_snapshot(self):
        bt = GoldBacktest()
        prepared = {
            "raw": pd.DataFrame({"High": [2.0], "Low": [1.0]}),
            "features": pd.DataFrame(index=pd.to_datetime(["2026-01-01T00:00:00Z"])),
            "requested_bars": 5000,
            "received_bars": 5000,
            "history_partial": False,
        }
        seen = []

        def fake_run(tf, bars=None, retrain_every=None, min_score_override=None,
                     min_ai_override=None, _prepared=None):
            seen.append(_prepared)
            return {
                "trades": 2, "win_rate": 50.0, "net_r": 1.0,
                "profit_factor": 2.0, "max_drawdown_r": 1.0,
                "expectancy_r": 0.5, "raw_bars": 5000,
                "usable_bars": 1, "history_partial": False,
            }

        with patch.object(bt, "_prepare", return_value=prepared) as prepare, \
             patch.object(bt, "run", side_effect=fake_run):
            result = bt.run_grid("15m", bars=5000, scores=(60, 65),
                                 ai_probs=(0.40, 0.50))

        prepare.assert_called_once_with("15m", 5000)
        self.assertEqual(len(seen), 4)
        self.assertTrue(all(snapshot is prepared for snapshot in seen))
        self.assertEqual(result["trials"], 4)
        self.assertEqual(result["raw_bars"], 5000)
        self.assertEqual(result["usable_bars"], 1)
        self.assertEqual(result["snapshot_start"], "2026-01-01T00:00:00+00:00")
        self.assertEqual(result["snapshot_end"], "2026-01-01T00:00:00+00:00")

    def test_requested_bars_uses_timeframe_specific_config(self):
        self.assertEqual(GoldBacktest._requested_bars("5m"), 10000)
        self.assertEqual(GoldBacktest._requested_bars("15m"), 5000)
        self.assertEqual(GoldBacktest._requested_bars("1h"), 10000)
        self.assertEqual(GoldBacktest._requested_bars("15m", 1234), 1234)


if __name__ == "__main__":
    unittest.main()
