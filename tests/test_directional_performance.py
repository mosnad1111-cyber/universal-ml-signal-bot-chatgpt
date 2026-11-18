import unittest
from unittest.mock import Mock

import config
from engine import Engine


class DirectionalPerformanceTests(unittest.TestCase):
    def _trade(self, symbol, direction, result, trade_id, timeframe="5m"):
        return {
            "id": trade_id,
            "symbol": symbol,
            "timeframe": timeframe,
            "direction": direction,
            "entry": 100.0,
            "sl": 99.0 if direction == "BUY" else 101.0,
            "tp": 102.0 if direction == "BUY" else 98.0,
            "close_price": 102.0 if result == "TP" else 99.0 if direction == "BUY" else 101.0,
            "status": "CLOSED",
            "result": result,
            "closed_at": f"2026-11-1{trade_id}T12:00:00+00:00",
            "score": 65.0,
            "ai_prob": 0.4,
        }

    def test_directional_report_includes_cost_sensitivity_and_ai_diagnostics(self):
        btc = config.ASSETS["BTC"]["symbol"]
        rows = [
            self._trade(btc, "BUY", "TP", 1),
            self._trade(btc, "SELL", "SL", 2),
            {
                "symbol": btc, "timeframe": "15m", "direction": "BUY",
                "status": "ACTIVE", "result": None,
            },
        ]
        engine = Engine.__new__(Engine)
        engine.db = Mock()
        engine.db.recent.return_value = rows
        metric = {
            "engine": "XGBoost", "auc": 0.56, "accuracy": 0.70,
            "precision": 0.60, "recall": 0.55, "pr_auc": 0.42,
            "training_rows": 500, "deployment_training_rows": 500,
            "validation_rows": 100, "validation_positive_rate": 0.30,
            "validation_baseline_accuracy": 0.70,
            "buy_validation_rows": 50, "buy_auc": 0.58,
            "buy_precision": 0.62, "buy_recall": 0.56, "buy_pr_auc": 0.44,
            "sell_validation_rows": 50, "sell_auc": 0.54,
            "sell_precision": 0.58, "sell_recall": 0.54, "sell_pr_auc": 0.40,
        }
        engine.ai = {
            asset["symbol"]: Mock(info=Mock(return_value={"5m": metric}))
            for asset in config.ASSETS.values()
        }

        report = engine.stats_text()

        self.assertIn("تفصيل الفريمات والاتجاهات", report)
        self.assertIn("🟢 شراء", report)
        self.assertIn("🔴 بيع", report)
        self.assertIn("بعد تكلفة افتراضية 0.05R", report)
        self.assertIn("بعد تكلفة افتراضية 0.10R", report)
        self.assertIn("PR-AUC", report)
        self.assertIn("Precision", report)
        self.assertIn("Recall", report)
        self.assertIn("مفعّلة: 1", report)

    def test_cost_adjustment_subtracts_cost_per_trade(self):
        trades = [
            {"_r": 2.0, "closed_at": "2026-11-01T00:00:00Z", "id": 1},
            {"_r": -1.0, "closed_at": "2026-11-02T00:00:00Z", "id": 2},
        ]
        summary = Engine._cost_adjusted_summary(trades, 0.05)
        self.assertAlmostEqual(summary["net_r"], 0.90)
        self.assertAlmostEqual(summary["expectancy"], 0.45)
        self.assertEqual(summary["trades"], 2)


if __name__ == "__main__":
    unittest.main()
