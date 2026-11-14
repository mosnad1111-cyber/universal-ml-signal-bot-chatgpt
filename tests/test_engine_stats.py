import unittest

from config import ASSETS
from engine import Engine


class FakeDB:
    def __init__(self, rows):
        self.rows = rows

    def recent(self, limit):
        return self.rows[:limit]


class FakeAI:
    errors = {}

    def info(self):
        return {
            "5m": {
                "engine": "XGBoost", "auc": 0.61, "accuracy": 0.70,
                "training_rows": 1200, "validation_rows": 200,
                "validation_positive_rate": 0.45,
                "validation_baseline_accuracy": 0.55,
                "buy_auc": 0.63, "sell_auc": 0.58,
            }
        }


class DetailedStatisticsTests(unittest.TestCase):
    def test_realized_r_handles_tp_sl_and_directional_timeout(self):
        self.assertAlmostEqual(Engine._realized_r({
            "entry": 100, "sl": 90, "tp": 120, "result": "TP", "direction": "BUY",
        }), 2.0)
        self.assertEqual(Engine._realized_r({
            "entry": 100, "sl": 110, "tp": 80, "result": "SL", "direction": "SELL",
        }), -1.0)
        self.assertAlmostEqual(Engine._realized_r({
            "entry": 100, "sl": 110, "tp": 80, "result": "TIMEOUT",
            "close_price": 95, "direction": "SELL",
        }), 0.5)

    def test_performance_summary_reports_expectancy_pf_drawdown_and_streaks(self):
        trades = [{"_r": value, "closed_at": str(index), "id": index}
                  for index, value in enumerate([2.0, -1.0, -1.0, 3.0])]
        metrics = Engine._performance_summary(trades)
        self.assertEqual(metrics["trades"], 4)
        self.assertEqual(metrics["wins"], 2)
        self.assertEqual(metrics["losses"], 2)
        self.assertAlmostEqual(metrics["net_r"], 3.0)
        self.assertAlmostEqual(metrics["expectancy"], 0.75)
        self.assertAlmostEqual(metrics["profit_factor"], 2.5)
        self.assertAlmostEqual(metrics["max_drawdown"], 2.0)
        self.assertEqual(metrics["max_loss_streak"], 2)

    def test_telegram_stats_include_asset_timeframe_direction_and_cost_caveats(self):
        gold = ASSETS["GOLD"]["symbol"]
        btc = ASSETS["BTC"]["symbol"]
        rows = [
            {
                "id": 1, "symbol": gold, "timeframe": "5m", "direction": "BUY",
                "entry": 100, "sl": 90, "tp": 120, "score": 70, "ai_prob": 0.65,
                "status": "CLOSED", "result": "TP", "close_price": 120,
                "closed_at": "2026-11-01T00:00:00+00:00",
            },
            {
                "id": 2, "symbol": gold, "timeframe": "5m", "direction": "SELL",
                "entry": 100, "sl": 110, "tp": 80, "score": 68, "ai_prob": 0.58,
                "status": "CLOSED", "result": "SL", "close_price": 110,
                "closed_at": "2026-11-02T00:00:00+00:00",
            },
            {
                "id": 3, "symbol": btc, "timeframe": "1h", "direction": "BUY",
                "entry": 50000, "sl": 49000, "tp": 52000, "score": 72, "ai_prob": 0.61,
                "status": "WAITING", "result": None, "close_price": None,
                "closed_at": None,
            },
        ]
        engine = Engine.__new__(Engine)
        engine.db = FakeDB(rows)
        engine.ai = {gold: FakeAI(), btc: FakeAI()}
        report = engine.stats_text()
        self.assertIn("Expectancy", report)
        self.assertIn("Profit Factor", report)
        self.assertIn("تفصيل الفريمات والاتجاهات", report)
        self.assertIn("شراء", report)
        self.assertIn("بيع", report)
        self.assertIn("AUC BUY", report)
        self.assertIn("قبل السبريد والانزلاق", report)


if __name__ == "__main__":
    unittest.main()
