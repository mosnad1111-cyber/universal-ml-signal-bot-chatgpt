import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from ai_engine import _label_direction
from backtest import GoldBacktest


class BacktestMetricTests(unittest.TestCase):
    def test_score_bands_cover_scores_below_65_correctly(self):
        expected = {
            59.9: "<60",
            60.0: "60-64",
            64.9: "60-64",
            65.0: "65-69",
            69.9: "65-69",
            70.0: "70-74",
            75.0: "75-79",
            80.0: "80+",
        }
        for score, band in expected.items():
            with self.subTest(score=score):
                self.assertEqual(GoldBacktest._score_band(score), band)

    def test_group_win_rate_counts_profitable_timeouts(self):
        trades = [
            {"direction": "BUY", "result": "TP", "r": 2.0},
            {"direction": "BUY", "result": "SL", "r": -1.0},
            {"direction": "BUY", "result": "TIMEOUT", "r": 0.5},
        ]
        groups = GoldBacktest._groups(trades, "direction")
        self.assertEqual(groups[0]["n"], 3)
        self.assertAlmostEqual(groups[0]["wr"], 200.0 / 3.0)

    def test_timeout_streaks_follow_realized_r_not_exit_label(self):
        trades = [
            {"result": "TIMEOUT", "r": 0.5},
            {"result": "TIMEOUT", "r": -0.3},
            {"result": "TP", "r": 2.0},
            {"result": "SL", "r": -1.0},
            {"result": "TIMEOUT", "r": 0.0},
        ]
        stats = GoldBacktest._sequence_stats(trades)
        self.assertEqual(stats["max_win_streak"], 1)
        self.assertEqual(stats["max_loss_streak"], 1)

    def test_training_label_accepts_entry_after_old_pending_expiry(self):
        n = 60
        x = pd.DataFrame({
            "Close": np.full(n, 12.0),
            "High": np.full(n, 12.5),
            "Low": np.full(n, 11.5),
            "atr": np.full(n, 3.0),
            "high20": np.full(n, 20.0),
            "low20": np.full(n, 10.0),
        })
        # The entry first becomes touchable after the 12-bar pending window.
        x.loc[13, "High"] = 13.0
        x.loc[14, "High"] = 20.0
        labels = _label_direction(x, direction=1, horizon=48)
        self.assertEqual(labels.iloc[0], 1.0)

    def test_training_label_accepts_entry_at_pending_deadline(self):
        n = 60
        x = pd.DataFrame({
            "Close": np.full(n, 12.0),
            "High": np.full(n, 12.5),
            "Low": np.full(n, 11.5),
            "atr": np.full(n, 3.0),
            "high20": np.full(n, 20.0),
            "low20": np.full(n, 10.0),
        })
        # Entry at age 12 is still allowed, matching the live monitor.
        x.loc[12, "High"] = 13.0
        x.loc[13, "High"] = 20.0
        labels = _label_direction(x, direction=1, horizon=48)
        self.assertEqual(labels.iloc[0], 1.0)

    def test_grid_search_uses_actual_15m_thresholds_for_baseline(self):
        bt = GoldBacktest()
        index = pd.date_range("2026-01-01", periods=1200, freq="15min", tz="UTC")
        prepared = {
            "features": pd.DataFrame(index=index),
            "raw": pd.DataFrame(index=index),
            "received_bars": 1200,
            "history_partial": False,
            "snapshot_id": "unit-test-snapshot",
            "snapshot_reused": False,
        }
        mocked_result = {
            "trades": 1, "win_rate": 100.0, "net_r": 2.0,
            "profit_factor": float("inf"), "max_drawdown_r": 0.0,
            "expectancy_r": 2.0, "raw_bars": 1200,
            "completed_bars": 1200, "usable_bars": 1200,
            "history_partial": False,
        }
        with patch.object(bt, "_prepare", return_value=prepared), \
             patch.object(bt, "_walk_forward_predictions", return_value={}), \
             patch.object(bt, "run", return_value=mocked_result):
            result = bt.run_grid("15m", bars=1200, scores=(65,), ai_probs=(0.30,))
        self.assertEqual(result["default_score"], 65.0)
        self.assertEqual(result["default_ai"], 0.30)
        self.assertIsNotNone(result["baseline"])


if __name__ == "__main__":
    unittest.main()
