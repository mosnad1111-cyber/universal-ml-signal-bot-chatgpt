import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from scripts.prepare_lean_strategy_lab import _lean_capital_requirements, _write_bars, _write_strategy
from scripts.summarize_lean_strategy_lab import _json_safe_metrics, _strategy_metrics


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

    def test_writes_decision_only_strategy_metadata_and_signals(self):
        _write_bars(self.tf_dir, self.raw)
        common = {
            "request_id": "a" * 32,
            "asset_key": "GOLD",
            "timeframe": "5m",
            "market_data_source": "Dukascopy XAU/USD BID historical OHLC",
            "bars_count": 3,
            "raw_bars": 3,
            "completed_bars": 3,
            "usable_bars": 3,
        }
        result = {
            "decision_only": True,
            "setup_candidates": 1,
            "model_fit_diagnostics": {"fit_attempts": 2, "fit_failures": 0},
            "lean_events": [{
                "signal_time": self.index[1].isoformat(),
                "direction": "BUY",
                "entry": 101.0,
                "sl": 100.0,
                "tp": 103.0,
                "risk": 1.0,
                "score": 75.0,
                "ai_prob": 0.8,
            }],
        }

        written = _write_strategy(self.tf_dir, "baseline", result, common)
        self.assertEqual(written["status"], "ready")
        self.assertEqual(written["metadata"]["signals_count"], 1)
        self.assertTrue(written["metadata"]["decision_only"])
        self.assertEqual(
            json.loads((self.tf_dir / "strategies/baseline/signals.json").read_text())[0]["direction"],
            "BUY",
        )

    def test_rejects_strategy_decision_not_mapped_to_original_candle(self):
        _write_bars(self.tf_dir, self.raw)
        result = {
            "decision_only": True,
            "lean_events": [{
                "signal_time": "2026-10-01T00:20:00+00:00",
                "direction": "BUY", "entry": 100, "sl": 99, "tp": 102,
                "risk": 1, "score": 70, "ai_prob": 0.8,
            }],
        }
        with self.assertRaisesRegex(RuntimeError, "do not match original OHLC"):
            _write_strategy(self.tf_dir, "baseline", result, {})

    def test_calibration_excludes_trades_crossing_the_midpoint(self):
        metadata = {"midpoint": "2026-10-01T00:10:00+00:00"}
        report = {
            "trades_detail": [
                {"signal_time": "2026-10-01T00:00:00+00:00",
                 "exit_time": "2026-10-01T00:05:00+00:00", "gross_r": 2.0},
                {"signal_time": "2026-10-01T00:05:00+00:00",
                 "exit_time": "2026-10-01T00:15:00+00:00", "gross_r": -1.0},
                {"signal_time": "2026-10-01T00:15:00+00:00",
                 "exit_time": "2026-10-01T00:20:00+00:00", "gross_r": -1.0},
            ],
            "unfilled_setups": 1,
            "unresolved_trades": 0,
        }
        metrics = _strategy_metrics(metadata, report)
        self.assertEqual(metrics["calibration"]["trades"], 1)
        self.assertEqual(metrics["holdout"]["trades"], 1)
        self.assertAlmostEqual(metrics["calibration"]["net_r"], 2.0)
        self.assertAlmostEqual(metrics["holdout"]["net_r"], -1.0)

    def test_infinite_profit_factor_becomes_json_safe(self):
        metrics = _json_safe_metrics({"profit_factor": float("inf"), "net_r": 2.0})
        self.assertEqual(metrics["profit_factor"], "Infinity")
        json.dumps(metrics, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
