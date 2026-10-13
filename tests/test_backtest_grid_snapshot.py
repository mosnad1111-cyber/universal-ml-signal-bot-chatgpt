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
        predictions = {700: {"BUY": 0.7, "SELL": 0.3}}

        def fake_run(tf, bars=None, retrain_every=None, min_score_override=None,
                     min_ai_override=None, _prepared=None, _predictions=None):
            seen.append((_prepared, _predictions, min_score_override, min_ai_override))
            score = float(min_score_override if min_score_override is not None else 65)
            ai = float(min_ai_override if min_ai_override is not None else 0.50)
            net = score / 100 + ai
            return {
                "trades": 2, "win_rate": 50.0, "net_r": net,
                "profit_factor": 2.0, "max_drawdown_r": 1.0,
                "expectancy_r": net / 2, "raw_bars": 5000,
                "usable_bars": 1, "history_partial": False,
            }

        with patch.object(bt, "_prepare", return_value=prepared) as prepare, \
             patch.object(bt, "_walk_forward_predictions", return_value=predictions) as predict, \
             patch.object(bt, "run", side_effect=fake_run):
            baseline = bt.run("15m", bars=5000, min_score_override=65,
                              min_ai_override=0.50, _prepared=prepared,
                              _predictions=predictions)
            result = bt.run_grid("15m", bars=5000, scores=(60, 65),
                                 ai_probs=(0.40, 0.50))

        self.assertEqual(prepare.call_count, 2)  # baseline snapshot + one Grid Search snapshot
        predict.assert_called_once()
        self.assertEqual(len(seen), 5)  # baseline + four threshold combinations
        self.assertTrue(all(snapshot is prepared and pred is predictions
                            for snapshot, pred, _, _ in seen))
        matching = next(row for row in result["rows"]
                        if row["score"] == 65 and row["ai"] == 0.50)
        self.assertAlmostEqual(baseline["net_r"], matching["net_r"])
        self.assertAlmostEqual(baseline["expectancy_r"], matching["e"])
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
