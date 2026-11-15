import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from scripts.prepare_lean_strategy_lab import (
    _lean_capital_requirements,
    _write_bars,
    _write_candidate,
    threshold_axes,
    threshold_id,
    TIMEFRAME_BARS,
)
from scripts.summarize_lean_strategy_lab import (
    _candidate_metrics,
    _json_safe_metrics,
    select_calibration_candidates,
)


class LeanStrategyLabTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.tf_dir = Path(self.temp.name) / "5m"
        self.tf_dir.mkdir()
        self.index = pd.date_range("2026-10-01T00:00:00Z", periods=3, freq="5min")
        self.raw = pd.DataFrame({
            "Open": [100.0, 101.0, 102.0],
            "High": [101.0, 102.0, 103.0],
            "Low": [99.0, 100.0, 101.0],
            "Close": [100.5, 101.5, 102.5],
            "Volume": [0.0, 0.0, 0.0],
        }, index=self.index)

    def test_strategy_lab_history_target_is_20000_bars_per_timeframe(self):
        self.assertEqual(TIMEFRAME_BARS, {"5m": 20000, "15m": 20000, "1h": 20000})

    def test_summarizer_cli_imports_project_module_outside_repo_cwd(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "summarize_lean_strategy_lab.py"
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        result = subprocess.run(
            [sys.executable, str(script), "--help"],
            cwd=self.temp.name,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("--prepared-dir", result.stdout)

    def test_lean_cash_covers_one_unit_above_100k_with_margin_buffer(self):
        raw = self.raw.copy()
        raw.loc[self.index[1], "High"] = 150000.0
        max_price, initial_cash = _lean_capital_requirements(raw)
        self.assertEqual(max_price, 150000.0)
        self.assertEqual(initial_cash, 225000.0)
        self.assertGreaterEqual(initial_cash, max_price * 1.5)

    def test_lean_cash_keeps_existing_floor_for_lower_priced_markets(self):
        max_price, initial_cash = _lean_capital_requirements(self.raw)
        self.assertEqual(max_price, 103.0)
        self.assertEqual(initial_cash, 100000.0)

    def test_writes_one_chronological_real_ohlc_stream(self):
        count, daily = _write_bars(self.tf_dir, self.raw)
        self.assertEqual(count, 3)
        self.assertEqual(daily, {"20261001": 3})
        lines = (self.tf_dir / "bars.csv").read_text().splitlines()
        self.assertEqual(len(lines), 4)
        self.assertIn("2026-10-01 00:00:00.000000", lines[1])

    def _common(self):
        return {
            "request_id": "a" * 32,
            "asset_key": "GOLD",
            "symbol": "TVC:GOLD",
            "timeframe": "5m",
            "market_data_source": "Dukascopy XAU/USD BID historical OHLC",
            "market_data_type": "real_historical_ohlc",
            "start": self.index[0].isoformat(),
            "end": self.index[-1].isoformat(),
            "bars_count": 3,
            "raw_bars": 3,
            "completed_bars": 3,
            "usable_bars": 3,
        }

    def _result(self, signal_time):
        return {
            "decision_only": True,
            "setup_candidates": 1,
            "model_fit_diagnostics": {"fit_attempts": 2, "fit_failures": 0},
            "lean_events": [{
                "signal_time": signal_time,
                "direction": "BUY",
                "entry": 101.0,
                "sl": 100.0,
                "tp": 103.0,
                "risk": 1.0,
                "score": 75.0,
                "ai_prob": 0.8,
            }],
        }

    def test_writes_strategy_threshold_candidate_metadata_and_signals(self):
        _write_bars(self.tf_dir, self.raw)
        written = _write_candidate(
            self.tf_dir, "baseline", self._result(self.index[1].isoformat()),
            self._common(), 60, 0.30, 60, 0.30,
        )
        self.assertEqual(written["status"], "ready")
        self.assertEqual(written["candidate_id"], "baseline__score_060_ai_030")
        metadata = written["metadata"]
        self.assertEqual(metadata["strategy_type"], "baseline")
        self.assertEqual(metadata["score_threshold"], 60.0)
        self.assertEqual(metadata["ai_threshold"], 0.30)
        self.assertTrue(metadata["is_live_baseline"])
        signals_path = (
            self.tf_dir / "strategies/baseline/candidates/score_060_ai_030/signals.json"
        )
        self.assertEqual(json.loads(signals_path.read_text())[0]["direction"], "BUY")

    def test_rejects_strategy_decision_not_mapped_to_original_candle(self):
        _write_bars(self.tf_dir, self.raw)
        with self.assertRaisesRegex(RuntimeError, "do not match original OHLC"):
            _write_candidate(
                self.tf_dir, "baseline", self._result("2026-10-01T00:20:00+00:00"),
                self._common(), 60, 0.30, 60, 0.30,
            )

    def test_threshold_axes_include_the_live_pair_without_duplicates(self):
        scores, ais, live_score, live_ai = threshold_axes("5m")
        self.assertIn(live_score, scores)
        self.assertIn(live_ai, ais)
        pairs = [(score, ai) for score in scores for ai in ais]
        self.assertEqual(len(pairs), len(set(pairs)))
        self.assertEqual(threshold_id(live_score, live_ai), threshold_id(float(live_score), float(live_ai)))

    def test_calibration_and_directional_splits_exclude_crossing_trades(self):
        metadata = {
            "midpoint": "2026-01-03T00:00:00+00:00",
            "candidate_id": "baseline__score_060_ai_030",
            "strategy_type": "baseline",
            "strategy_name": "الاستراتيجية الحالية",
            "threshold_id": "score_060_ai_030",
            "score_threshold": 60.0,
            "ai_threshold": 0.30,
            "signals_count": 3,
        }
        report = {
            "trades_detail": [
                {"signal_time": "2026-01-01T00:00:00+00:00", "exit_time": "2026-01-01T01:00:00+00:00", "direction": "BUY", "gross_r": 2.0, "fee_r": 0.05, "net_r": 1.95},
                {"signal_time": "2026-01-02T00:00:00+00:00", "exit_time": "2026-01-04T01:00:00+00:00", "direction": "SELL", "gross_r": -1.0, "fee_r": 0.05, "net_r": -1.05},
                {"signal_time": "2026-01-04T00:00:00+00:00", "exit_time": "2026-01-04T01:00:00+00:00", "direction": "SELL", "gross_r": -1.0, "fee_r": 0.05, "net_r": -1.05},
            ],
            "unfilled_setups": 1,
            "unresolved_trades": 0,
        }
        metrics = _candidate_metrics(metadata, report)
        self.assertEqual(metrics["calibration_gross"]["trades"], 1)
        self.assertEqual(metrics["holdout_gross"]["trades"], 1)
        self.assertAlmostEqual(metrics["calibration_net"]["net_after_cost_r"], 1.95)
        self.assertAlmostEqual(metrics["holdout_net"]["net_after_cost_r"], -1.05)
        self.assertEqual(metrics["directional"]["BUY"]["calibration_net"]["trades"], 1)
        self.assertEqual(metrics["directional"]["BUY"]["holdout_net"]["trades"], 0)
        self.assertEqual(metrics["directional"]["SELL"]["calibration_net"]["trades"], 0)
        self.assertEqual(metrics["directional"]["SELL"]["holdout_net"]["trades"], 1)
        self.assertAlmostEqual(
            metrics["directional"]["SELL"]["holdout_cost_005R"]["net_after_cost_r"], -1.05
        )
        self.assertAlmostEqual(
            metrics["directional"]["SELL"]["holdout_cost_010R"]["net_after_cost_r"], -1.10
        )

    def test_no_trade_direction_is_still_present_in_report_metrics(self):
        metadata = {
            "midpoint": "2026-01-03T00:00:00+00:00",
            "candidate_id": "baseline__score_060_ai_030",
            "strategy_type": "baseline",
            "strategy_name": "الاستراتيجية الحالية",
            "threshold_id": "score_060_ai_030",
            "score_threshold": 60.0,
            "ai_threshold": 0.30,
            "signals_count": 0,
        }
        metrics = _candidate_metrics(metadata, {"trades_detail": []})
        self.assertEqual(set(metrics["directional"]), {"BUY", "SELL"})
        self.assertEqual(metrics["directional"]["BUY"]["all_net"]["trades"], 0)
        self.assertEqual(metrics["directional"]["SELL"]["all_net"]["trades"], 0)

    def test_candidate_selection_uses_calibration_only(self):
        eligible = {
            "candidate_id": "candidate_a",
            "calibration_net": {
                "trades": 30, "net_after_cost_r": 3.6,
                "expectancy_after_cost_r": 0.12, "profit_factor": 1.5,
            },
            "holdout_net": {"trades": 30, "net_after_cost_r": -90.0, "profit_factor": 0.01},
        }
        holdout_only_winner = {
            "candidate_id": "candidate_b",
            "calibration_net": {
                "trades": 30, "net_after_cost_r": -1.0,
                "expectancy_after_cost_r": -0.03, "profit_factor": 0.8,
            },
            "holdout_net": {"trades": 30, "net_after_cost_r": 100.0, "profit_factor": 99.0},
        }
        selected, ranked = select_calibration_candidates([eligible, holdout_only_winner])
        self.assertEqual(selected["candidate_id"], "candidate_a")
        self.assertEqual([item["candidate_id"] for item in ranked], ["candidate_a"])

    def test_infinite_profit_factor_becomes_json_safe(self):
        metrics = _json_safe_metrics({"profit_factor": float("inf"), "net_r": 2.0})
        self.assertEqual(metrics["profit_factor"], "Infinity")
        json.dumps(metrics, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
