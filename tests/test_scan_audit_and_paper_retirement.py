import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from db import DB


class ScanAuditAndPaperRetirementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {"DATABASE_URL": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.db = DB(str(Path(self.temp.name) / "audit.sqlite3"))

    def test_scan_audit_persists_both_direction_candidates_and_context(self):
        self.db.record_scan_event({
            "symbol": "BINANCE:BTCUSDT", "timeframe": "5m",
            "candle_time": "2026-11-21T10:00:00+00:00",
            "best_direction": "SELL", "reject": "against_higher_timeframe_trend",
            "buy_ai": 0.41, "sell_ai": 0.62,
            "buy_score": 58.0, "sell_score": 72.0,
            "context_trend": -1, "ctx15_trend": -1, "ctx1h_trend": -1,
        })
        rows = self.db.recent_scan_audit(10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["symbol"], "BINANCE:BTCUSDT")
        self.assertEqual(rows[0]["context_trend"], -1)
        self.assertEqual(rows[0]["reject_reason"], "against_higher_timeframe_trend")

    def test_retiring_paper_trial_deletes_only_its_ledger_and_settings(self):
        self.db.set_setting("paper_trial_start_utc", "old")
        self.db.set_setting("telegram_chat_id", "123")
        self.db.delete_paper_trial("btc_1h_trend_pullback_score65_ai60_v1")
        self.assertIsNone(self.db.get_setting("paper_trial_start_utc"))
        self.assertEqual(self.db.get_setting("telegram_chat_id"), "123")
        self.assertEqual(self.db.recent_scan_audit(10), [])


if __name__ == "__main__":
    unittest.main()
