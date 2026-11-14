import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from scripts.prepare_lean_threshold_test import _write_candidate, threshold_axes, threshold_id
from scripts.summarize_lean_threshold_test import (
    _candidate_metrics,
    select_calibration_candidates,
)


class LeanThresholdPreparationTests(unittest.TestCase):
    def test_threshold_id_is_stable_and_encodes_both_values(self):
        self.assertEqual(threshold_id(60, 0.30), "score_060_ai_030")
        self.assertEqual(threshold_id(75, 0.60), "score_075_ai_060")

    def test_grid_includes_24_combinations_and_live_baseline_for_each_timeframe(self):
        from config import thresholds_for
        for timeframe in ("5m", "15m", "1h"):
            scores, ai_values, live_score, live_ai = threshold_axes(timeframe)
            self.assertEqual(len(scores) * len(ai_values), 24)
            self.assertIn(live_score, scores)
            self.assertIn(live_ai, ai_values)

    def test_candidate_writes_decision_only_signals_aligned_to_shared_ohlc(self):
        with tempfile.TemporaryDirectory() as tmp:
            timeframe_dir = Path(tmp) / "5m"
            timeframe_dir.mkdir()
            raw = pd.DataFrame(
                {
                    "Open": [100.0, 101.0],
                    "High": [102.0, 103.0],
                    "Low": [99.0, 100.0],
                    "Close": [101.0, 102.0],
                    "Volume": [0.0, 0.0],
                },
                index=pd.to_datetime(
                    ["2026-10-01T00:00:00Z", "2026-10-01T00:05:00Z"], utc=True
                ),
            )
            from scripts.prepare_lean_strategy_lab import _write_bars
            _write_bars(timeframe_dir, raw)
            result = {
                "decision_only": True,
                "setup_candidates": 1,
                "lean_events": [{
                    "signal_time": "2026-10-01T00:00:00+00:00",
                    "direction": "BUY", "entry": 100.0, "sl": 99.0, "tp": 102.0,
                    "risk": 1.0, "score": 70.0, "ai_prob": 0.5,
                }],
                "model_fit_diagnostics": {"fit_attempts": 3},
            }
            common = {
                "timeframe": "5m",
                "market_data_source": "Dukascopy XAU/USD BID historical OHLC",
                "market_data_type": "real_historical_ohlc",
                "fee_r": 0.05,
            }
            written = _write_candidate(
                timeframe_dir, "score_060_ai_050", result, common, 60, 0.5, False
            )
            self.assertEqual(written["status"], "ready")
            metadata = json.loads(
                (timeframe_dir / "thresholds" / "score_060_ai_050" / "metadata.json").read_text()
            )
            signals = json.loads(
                (timeframe_dir / "thresholds" / "score_060_ai_050" / "signals.json").read_text()
            )
            self.assertTrue(metadata["decision_only"])
            self.assertEqual(metadata["score_threshold"], 60.0)
            self.assertEqual(metadata["ai_threshold"], 0.5)
            self.assertEqual(len(signals), 1)

    def test_candidate_rejects_signal_not_aligned_to_original_bars(self):
        with tempfile.TemporaryDirectory() as tmp:
            timeframe_dir = Path(tmp) / "5m"
            timeframe_dir.mkdir()
            raw = pd.DataFrame(
                {"Open": [100.0], "High": [101.0], "Low": [99.0], "Close": [100.0], "Volume": [0.0]},
                index=pd.to_datetime(["2026-10-01T00:00:00Z"], utc=True),
            )
            from scripts.prepare_lean_strategy_lab import _write_bars
            _write_bars(timeframe_dir, raw)
            result = {
                "decision_only": True,
                "lean_events": [{
                    "signal_time": "2026-10-01T00:05:00+00:00",
                    "direction": "BUY", "entry": 100.0, "sl": 99.0, "tp": 102.0,
                }],
            }
            with self.assertRaises(RuntimeError):
                _write_candidate(timeframe_dir, "candidate", result, {}, 60, 0.5, False)


class LeanThresholdMetricsTests(unittest.TestCase):
    def test_lean_gross_and_net_metrics_use_trade_ledger_and_split_by_exit_time(self):
        report = {
            "trades_detail": [
                {"signal_time": "2026-01-01T00:00:00+00:00", "exit_time": "2026-01-01T01:00:00+00:00", "direction": "BUY", "gross_r": 2.0, "net_r": 1.95},
                {"signal_time": "2026-01-02T00:00:00+00:00", "exit_time": "2026-01-02T01:00:00+00:00", "direction": "SELL", "gross_r": -1.0, "net_r": -1.05},
                {"signal_time": "2026-01-04T00:00:00+00:00", "exit_time": "2026-01-04T01:00:00+00:00", "direction": "BUY", "gross_r": 2.0, "net_r": 1.95},
            ]
        }
        metrics = _candidate_metrics(
            {"midpoint": "2026-01-03T00:00:00+00:00", "score_threshold": 60, "ai_threshold": 0.3},
            report,
        )
        self.assertEqual(metrics["calibration_gross"]["trades"], 2)
        self.assertAlmostEqual(metrics["calibration_net"]["net_after_cost_r"], 0.90)
        self.assertEqual(metrics["holdout_net"]["trades"], 1)
        self.assertAlmostEqual(metrics["holdout_cost_010R"]["net_after_cost_r"], 1.90)
        self.assertEqual(metrics["directional"]["BUY"]["calibration_net"]["trades"], 1)
        self.assertAlmostEqual(
            metrics["directional"]["BUY"]["calibration_net"]["net_after_cost_r"], 1.95
        )
        self.assertEqual(metrics["directional"]["SELL"]["calibration_net"]["trades"], 1)
        self.assertAlmostEqual(
            metrics["directional"]["SELL"]["calibration_net"]["net_after_cost_r"], -1.05
        )
        self.assertEqual(metrics["directional"]["BUY"]["holdout_net"]["trades"], 1)
        self.assertEqual(metrics["directional"]["SELL"]["holdout_net"]["trades"], 0)

    def test_candidate_selection_uses_calibration_not_holdout(self):
        best_calibration = {
            "threshold_id": "calibration_winner",
            "calibration_net": {
                "trades": 30, "net_after_cost_r": 3.6,
                "expectancy_after_cost_r": 0.12, "profit_factor": 1.8,
            },
            "holdout_net": {"net_after_cost_r": -1000.0},
        }
        best_holdout = {
            "threshold_id": "holdout_winner",
            "calibration_net": {
                "trades": 30, "net_after_cost_r": 1.8,
                "expectancy_after_cost_r": 0.06, "profit_factor": 1.2,
            },
            "holdout_net": {"net_after_cost_r": 10000.0},
        }
        too_small = {
            "threshold_id": "small_sample",
            "calibration_net": {
                "trades": 29, "net_after_cost_r": 99.0,
                "expectancy_after_cost_r": 3.4, "profit_factor": 5.0,
            },
            "holdout_net": {"net_after_cost_r": 100000.0},
        }
        selected, top = select_calibration_candidates(
            [best_holdout, too_small, best_calibration]
        )
        self.assertEqual(selected["threshold_id"], "calibration_winner")
        self.assertEqual([item["threshold_id"] for item in top], [
            "calibration_winner", "holdout_winner"
        ])


if __name__ == "__main__":
    unittest.main()
