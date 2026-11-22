import os
import tempfile
import unittest

from db import DB


class ScanAuditIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DB(os.path.join(self.tmp.name, "audit.sqlite3"))

    def tearDown(self):
        self.tmp.cleanup()

    def sample(self, scanned_marker, score):
        return {
            "symbol": "BINANCE:BTCUSDT",
            "timeframe": "5m",
            "candle_time": "2026-11-22T10:00:00",
            "best_direction": "SELL",
            "reject": "score_below_threshold",
            "buy_ai": 0.31,
            "sell_ai": 0.62,
            "buy_score": score,
            "sell_score": 71.0,
            "context_trend": -1,
            "scan_marker": scanned_marker,
            "shadow_candidates": {
                "BUY": {"entry": 101.0, "sl": 99.0, "tp": 105.0},
                "SELL": {"entry": 100.0, "sl": 102.0, "tp": 96.0},
            },
        }

    def test_same_candle_is_upserted_not_duplicated(self):
        self.db.record_scan_event(self.sample("first", 61.0))
        self.db.record_scan_event(self.sample("second", 62.0))
        rows = self.db.scan_audit_page(limit=100)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["buy_score"], 62.0)
        self.assertEqual(rows[0]["buy_entry"], 101.0)
        self.assertEqual(rows[0]["sell_tp"], 96.0)

    def test_shadow_outcomes_resolve_and_summary_counts_them(self):
        self.db.record_scan_event(self.sample("first", 61.0))
        pending = self.db.pending_scan_audit("BINANCE:BTCUSDT", "5m")
        self.assertEqual(len(pending), 1)
        row_id = pending[0]["id"]
        self.db.resolve_scan_audit_candidate(row_id, "BUY", "TP", 7)
        self.db.resolve_scan_audit_candidate(row_id, "SELL", "SL", 4)
        pending_after = self.db.pending_scan_audit("BINANCE:BTCUSDT", "5m")
        self.assertEqual(len(pending_after), 0)
        summary = self.db.scan_audit_summary()
        self.assertEqual(summary[0]["buy_tp"], 1)
        self.assertEqual(summary[0]["sell_sl"], 1)

    def test_pagination_cursor(self):
        first = self.sample("first", 61.0)
        first["candle_time"] = "2026-11-22T10:00:00"
        second = self.sample("second", 62.0)
        second["candle_time"] = "2026-11-22T10:05:00"
        self.db.record_scan_event(first)
        self.db.record_scan_event(second)
        page1 = self.db.scan_audit_page(limit=1)
        page2 = self.db.scan_audit_page(limit=1, before_id=page1[0]["id"])
        self.assertEqual(len(page1), 1)
        self.assertEqual(len(page2), 1)
        self.assertNotEqual(page1[0]["id"], page2[0]["id"])


if __name__ == "__main__":
    unittest.main()
