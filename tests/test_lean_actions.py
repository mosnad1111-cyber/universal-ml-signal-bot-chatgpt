import json
import os
import unittest
from unittest.mock import Mock, patch

from lean_actions import LeanActionsClient, LeanActionsError


class FakeDB:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def get_setting(self, key, default=None):
        return self.values.get(key, default)

    def set_setting(self, key, value):
        self.values[key] = str(value)


class LeanActionsTests(unittest.TestCase):
    def make_client(self, db=None, session=None):
        with patch.dict(os.environ, {
            "GITHUB_ACTIONS_TOKEN": "test-token",
            "GITHUB_ACTIONS_REPOSITORY": "owner/repo",
            "LEAN_CALLBACK_BASE_URL": "https://bot.example.com",
        }, clear=False):
            client = LeanActionsClient(db or FakeDB(), session=session or Mock())
        return client

    def test_dispatch_starts_workflow_dispatch_with_real_backtest_inputs(self):
        session = Mock()
        response = Mock(status_code=204, text="")
        session.post.return_value = response
        client = self.make_client(session=session)

        client.dispatch("a" * 32, "BTC", ["5m", "1h"])

        args, kwargs = session.post.call_args
        self.assertIn("lean-real-backtest.yml/dispatches", args[0])
        self.assertEqual(kwargs["json"]["ref"], "main")
        self.assertEqual(kwargs["json"]["inputs"]["asset"], "BTC")
        self.assertEqual(kwargs["json"]["inputs"]["timeframes"], "5m,1h")
        self.assertEqual(
            kwargs["json"]["inputs"]["callback_url"],
            "https://bot.example.com/lean-backtest/callback",
        )

    def test_dispatch_rejects_synthetic_or_invalid_asset_selector(self):
        session = Mock()
        client = self.make_client(session=session)
        with self.assertRaises(LeanActionsError):
            client.dispatch("a" * 32, "FIXTURE", ["5m"])
        session.post.assert_not_called()

    def test_dispatch_requires_actions_token_and_https_callback(self):
        with patch.dict(os.environ, {
            "GITHUB_ACTIONS_TOKEN": "",
            "GITHUB_ACTIONS_REPOSITORY": "owner/repo",
            "LEAN_CALLBACK_BASE_URL": "",
            "RENDER_EXTERNAL_URL": "",
        }, clear=False):
            client = LeanActionsClient(FakeDB())
        with self.assertRaises(LeanActionsError):
            client.dispatch("a" * 32, "GOLD", ["5m"])

    def test_formatter_marks_historical_source_and_neutral_gold_volume(self):
        report = LeanActionsClient.format_report({
            "asset_key": "GOLD",
            "engine": "QuantConnect LEAN open-source engine",
            "timeframes": [{
                "metadata": {
                    "timeframe": "5m",
                    "market_data_source": "Dukascopy XAU/USD BID historical OHLC",
                    "raw_bars": 5000,
                    "completed_bars": 4999,
                    "usable_bars": 4700,
                    "snapshot_start": "2026-01-01T00:00:00+00:00",
                    "snapshot_end": "2026-02-01T00:00:00+00:00",
                    "signals_count": 15,
                    "volume_note": "Gold Volume is a constant neutral proxy, not traded volume.",
                },
                "lean_report": {
                    "trades": 4, "wins": 2, "losses": 2, "gross_r": 1.0,
                    "fees_r": 0.2, "lean_total_fees_usd": 0.4, "net_r": 0.8,
                    "unresolved_trades": 0, "unfilled_setups": 1,
                    "trades_detail": [{
                        "direction": "BUY", "result": "TP",
                        "entry_fill": 100, "exit_fill": 104, "net_r": 1.95,
                    }],
                },
            }],
        })
        self.assertIn("Dukascopy", report)
        self.assertIn("ليست حجم تداول حقيقي", report)
        self.assertIn("باكتيست تاريخي ببيانات سوق فعلية", report)
        self.assertIn("ليست عمولات وسيط فعلية", report)


if __name__ == "__main__":
    unittest.main()
