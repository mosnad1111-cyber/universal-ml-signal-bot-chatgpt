import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from db import DB


class PaperTradeLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {"DATABASE_URL": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.db = DB(str(Path(self.temp.name) / "paper.sqlite3"))

    def _trade(self):
        return {
            "trial_id": "btc_paper_trial_test",
            "symbol": "BINANCE:BTCUSDT",
            "timeframe": "1h",
            "strategy": "trend_pullback",
            "direction": "BUY",
            "entry": 100.0,
            "sl": 99.0,
            "tp": 102.0,
            "score": 70.0,
            "ai_prob": 0.65,
            "candle_time": "2026-11-15T10:00:00+00:00",
        }

    def test_paper_trade_lifecycle_is_persistent_and_separate(self):
        trade_id = self.db.paper_create(self._trade())
        self.assertIsNotNone(trade_id)
        self.assertIsNone(self.db.paper_create(self._trade()))

        waiting = self.db.paper_open(
            "btc_paper_trial_test", "BINANCE:BTCUSDT", "1h"
        )
        self.assertEqual(waiting["status"], "WAITING")
        self.db.paper_activate(trade_id, "2026-11-15T12:00:00+00:00")

        active = self.db.paper_open(
            "btc_paper_trial_test", "BINANCE:BTCUSDT", "1h"
        )
        self.assertEqual(active["status"], "ACTIVE")
        self.assertEqual(
            active["activation_candle_time"], "2026-11-15T12:00:00+00:00"
        )

        self.db.paper_close(trade_id, "TP", 102.0)
        self.assertIsNone(
            self.db.paper_open("btc_paper_trial_test", "BINANCE:BTCUSDT", "1h")
        )
        rows = self.db.paper_all("btc_paper_trial_test")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "CLOSED")
        self.assertEqual(rows[0]["result"], "TP")
        self.assertEqual(rows[0]["close_price"], 102.0)

    def test_paper_records_never_enter_live_signal_table(self):
        self.db.paper_create(self._trade())
        with self.db._conn() as conn:
            live_count = self.db._execute(
                conn, "SELECT COUNT(*) AS n FROM signals"
            ).fetchone()["n"]
            paper_count = self.db._execute(
                conn, "SELECT COUNT(*) AS n FROM paper_trades"
            ).fetchone()["n"]
        self.assertEqual(live_count, 0)
        self.assertEqual(paper_count, 1)


if __name__ == "__main__":
    unittest.main()
