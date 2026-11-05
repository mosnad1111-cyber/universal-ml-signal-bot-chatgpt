import unittest
from unittest.mock import patch

import pandas as pd

from backtest import GoldBacktest


class BacktestDecisionOnlyTests(unittest.TestCase):
    def test_decision_only_exports_all_setups_without_resolving_python_trades(self):
        index = pd.date_range("2026-10-01T00:00:00Z", periods=704, freq="5min")
        raw = pd.DataFrame({
            "Open": [100.0] * len(index),
            "High": [101.0] * len(index),
            "Low": [99.0] * len(index),
            "Close": [100.5] * len(index),
            "Volume": [0.0] * len(index),
        }, index=index)
        prepared = {
            "raw": raw,
            "features": raw.copy(),
            "requested_bars": len(raw),
            "received_bars": len(raw),
            "history_partial": False,
            "snapshot_id": "decision-only-fixture",
            "snapshot_start": index[0].isoformat(),
            "snapshot_end": index[-1].isoformat(),
            "market_data_source": "unit-test historical OHLC",
            "market_data_warning": "",
        }
        predictions = {
            700: {"BUY": 0.8, "SELL": 0.2},
            701: {"BUY": 0.8, "SELL": 0.2},
            702: {"BUY": 0.8, "SELL": 0.2},
            "_diagnostics": {"fit_attempts": 2, "fit_failures": 0},
        }
        setup = {
            "direction": "BUY",
            "entry": 100.0,
            "sl": 99.0,
            "tp": 102.0,
            "score": 75.0,
            "ai_prob": 0.8,
        }
        backtest = GoldBacktest()
        backtest._context = lambda *args, **kwargs: {}
        backtest._resolve = lambda *args, **kwargs: self.fail(
            "decision-only lab must never use the Python trade resolver"
        )

        with patch("backtest.build_setup", return_value=setup) as build_setup:
            result = backtest.run(
                "5m",
                bars=len(raw),
                _prepared=prepared,
                _predictions=predictions,
                symbol="TEST:OHLC",
                strategy="baseline",
                include_lean_payload=True,
                decision_only=True,
            )

        self.assertTrue(result["decision_only"])
        self.assertEqual(result["trades"], 0)
        self.assertEqual(len(result["lean_events"]), 3)
        self.assertEqual(result["setup_candidates"], 3)
        self.assertEqual(build_setup.call_count, 3)
        self.assertEqual(
            [event["signal_time"] for event in result["lean_events"]],
            [index[i].isoformat() for i in (700, 701, 702)],
        )
        self.assertEqual(len(result["market_bars"]), len(raw))


if __name__ == "__main__":
    unittest.main()
