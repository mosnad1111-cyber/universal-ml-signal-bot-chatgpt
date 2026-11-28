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
                "BUY": {"entry": 101.0, "sl": 99.0, "tp": 105.0, "ai_prob": 0.31, "score": score},
                "SELL": {"entry": 100.0, "sl": 102.0, "tp": 96.0, "ai_prob": 0.62, "score": 71.0},
            },
        }

    def test_same_candle_is_upserted_not_duplicated(self):
        self.db.record_scan_event(self.sample("first", 61.0))
        second = self.sample("second", 62.0)
        second["buy_ai"] = 0.35
        second["shadow_candidates"]["BUY"]["ai_prob"] = 0.35
        self.db.record_scan_event(second)
        rows = self.db.scan_audit_page(limit=100)
        self.assertEqual(len(rows), 1)
        # General scan telemetry reflects the latest rescan.
        self.assertEqual(rows[0]["buy_score"], 62.0)
        self.assertEqual(rows[0]["buy_ai"], 0.35)
        # Candidate geometry and its AI/Score snapshot remain from the same
        # original candidate that is later resolved to TP/SL.
        self.assertEqual(rows[0]["buy_entry"], 101.0)
        self.assertEqual(rows[0]["buy_candidate_ai"], 0.31)
        self.assertEqual(rows[0]["buy_candidate_score"], 61.0)
        self.assertEqual(rows[0]["sell_tp"], 96.0)
        self.assertEqual(rows[0]["sell_candidate_ai"], 0.62)

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


    def test_legacy_candidate_geometry_is_not_backfilled_with_later_scan_scores(self):
        sample = self.sample("legacy", 61.0)
        sample["shadow_candidates"]["BUY"]["ai_prob"] = None
        sample["shadow_candidates"]["BUY"]["score"] = None
        self.db.record_scan_event(sample)

        # Simulate a pre-migration row: geometry exists, candidate-specific
        # metadata columns were added later and are still NULL.
        with self.db._conn() as conn:
            conn.execute(
                "UPDATE scan_audit SET buy_candidate_ai=NULL, buy_candidate_score=NULL WHERE candle_time=?",
                ("2026-11-22T10:00:00",),
            )

        newer = self.sample("rescan", 69.0)
        newer["buy_ai"] = 0.48
        newer["shadow_candidates"]["BUY"]["ai_prob"] = 0.48
        newer["shadow_candidates"]["BUY"]["score"] = 69.0
        self.db.record_scan_event(newer)

        row = self.db.scan_audit_page(limit=10)[0]
        self.assertEqual(row["buy_entry"], 101.0)
        self.assertEqual(row["buy_score"], 69.0)  # latest scan telemetry
        self.assertIsNone(row["buy_candidate_ai"])
        self.assertIsNone(row["buy_candidate_score"])

    def test_legacy_duplicate_rows_are_deduplicated_on_migration(self):
        import sqlite3
        legacy_path = os.path.join(self.tmp.name, "legacy.sqlite3")
        conn = sqlite3.connect(legacy_path)
        conn.execute("""CREATE TABLE scan_audit(
            id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL, candle_time TEXT, scanned_at TEXT NOT NULL,
            best_direction TEXT, reject_reason TEXT, buy_ai REAL, sell_ai REAL,
            buy_score REAL, sell_score REAL, context_trend INTEGER,
            diagnostics_json TEXT NOT NULL)""")
        base = ("BINANCE:BTCUSDT", "5m", "2026-11-22T10:00:00", "2026-11-22T10:00:01",
                "BUY", "", 0.6, 0.4, 70.0, 50.0, 1, "{}")
        conn.execute("INSERT INTO scan_audit(symbol,timeframe,candle_time,scanned_at,best_direction,reject_reason,buy_ai,sell_ai,buy_score,sell_score,context_trend,diagnostics_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", base)
        newer = list(base); newer[3] = "2026-11-22T10:00:02"; newer[8] = 71.0
        conn.execute("INSERT INTO scan_audit(symbol,timeframe,candle_time,scanned_at,best_direction,reject_reason,buy_ai,sell_ai,buy_score,sell_score,context_trend,diagnostics_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", tuple(newer))
        conn.commit(); conn.close()
        migrated = DB(legacy_path)
        rows = migrated.scan_audit_page(limit=10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["buy_score"], 71.0)

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
