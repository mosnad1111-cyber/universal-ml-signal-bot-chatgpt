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


    def test_callback_accepts_uploaded_artifact_before_final_workflow_step_returns(self):
        request_id = "b" * 32
        db = FakeDB({
            "telegram_chat_id": "12345",
            "lean_active_request": request_id,
            # Simulate the failure notification sent by attempt 1. Attempt 2
            # must still be allowed to deliver the verified success report.
            "lean_delivered:456": "1",
            f"lean_request:{request_id}": json.dumps({
                "chat_id": "12345", "asset_key": "GOLD", "timeframes": ["5m"],
                "status": "dispatched",
            }),
        })
        client = self.make_client(db=db)
        run = {
            "path": ".github/workflows/lean-real-backtest.yml",
            "event": "workflow_dispatch",
            "display_title": f"LEAN historical backtest {request_id}",
            "head_branch": "main",
            "status": "in_progress",
            "conclusion": None,
            "html_url": "https://github.com/owner/repo/actions/runs/123",
        }
        client._get_json = Mock(side_effect=[
            run,
            {"jobs": [{"steps": [{"name": "Upload report", "conclusion": "success"}]}]},
        ])
        client._read_artifact = Mock(return_value={
            "synthetic_fixture": False,
            "market_data_type": "real_historical_ohlc",
            "asset_key": "GOLD",
            "timeframes": [{
                "metadata": {
                    "timeframe": "5m", "market_data_source": "Dukascopy XAU/USD BID",
                    "raw_bars": 1200,
                },
                "lean_report": {
                    "bars": 1200, "received_bars": 1200,
                    "order_errors": [], "trades": 0,
                },
            }],
        })

        class FakeTelegram:
            def __init__(self):
                self.sent = []
            def send_long(self, text, chat_id):
                self.sent.append((text, chat_id))
                return True
            def send(self, text, chat_id):
                self.sent.append((text, chat_id))
                return True

        telegram = FakeTelegram()
        result, status = client.handle_callback(123, request_id, telegram)
        self.assertEqual(status, 200)
        self.assertTrue(result["ok"])
        self.assertEqual(len(telegram.sent), 1)
        self.assertEqual(telegram.sent[0][1], "12345")
        self.assertEqual(db.get_setting("lean_active_request"), "")
        self.assertEqual(db.get_setting("lean_delivered:123"), "1")

    def test_callback_rejects_non_matching_workflow_run_title(self):
        request_id = "c" * 32
        db = FakeDB({
            "telegram_chat_id": "12345",
            f"lean_request:{request_id}": json.dumps({
                "chat_id": "12345", "asset_key": "BTC", "timeframes": ["1h"],
            }),
        })
        client = self.make_client(db=db)
        client._get_json = Mock(return_value={
            "path": ".github/workflows/lean-real-backtest.yml",
            "event": "workflow_dispatch",
            "display_title": "LEAN historical backtest other-request",
            "head_branch": "main",
        })
        result, status = client.handle_callback(123, request_id, Mock())
        self.assertEqual(status, 500)
        self.assertIn("Run title", result["error"])
        self.assertEqual(db.get_setting("lean_delivered:123"), None)


    def test_dispatch_starts_dedicated_lean_strategy_lab_workflow(self):
        session = Mock()
        session.post.return_value = Mock(status_code=204, text="")
        client = self.make_client(session=session)

        client.dispatch_strategy_lab("d" * 32, "GOLD", ["5m", "15m", "1h"])

        args, kwargs = session.post.call_args
        self.assertIn("lean-strategy-lab.yml/dispatches", args[0])
        self.assertEqual(kwargs["json"]["ref"], "main")
        self.assertEqual(kwargs["json"]["inputs"]["asset"], "GOLD")
        self.assertEqual(kwargs["json"]["inputs"]["timeframes"], "5m,15m,1h")

    def test_callback_accepts_only_verified_lean_strategy_lab_artifact(self):
        request_id = "e" * 32
        db = FakeDB({
            "telegram_chat_id": "12345",
            "lean_active_request": request_id,
            f"lean_request:{request_id}": json.dumps({
                "chat_id": "12345",
                "asset_key": "GOLD",
                "timeframes": ["5m"],
                "mode": "strategy_lab",
                "status": "dispatched",
            }),
        })
        client = self.make_client(db=db)
        client._get_json = Mock(side_effect=[
            {
                "path": ".github/workflows/lean-strategy-lab.yml",
                "event": "workflow_dispatch",
                "display_title": f"LEAN strategy lab {request_id}",
                "head_branch": "main",
                "run_attempt": 2,
                "status": "in_progress",
                "conclusion": None,
                "html_url": "https://github.com/owner/repo/actions/runs/456",
            },
            {"jobs": [{"steps": [{"name": "Upload report", "conclusion": "success"}]}]},
        ])
        executed_metadata = {
            "strategy": "baseline",
            "market_data_source": "Dukascopy XAU/USD BID historical OHLC",
            "raw_bars": 1200,
            "bars_count": 1200,
            "signals_count": 2,
        }
        executed_report = {
            "strategy": "baseline",
            "signals": 2,
            "bars": 1200,
            "received_bars": 1200,
            "reader_parsed_rows": 1200,
            "reader_unique_timestamps": 1200,
            "order_errors": [],
            "trades": 0,
        }
        names = ("baseline", "trend_pullback", "breakout_retest", "momentum_continuation")
        results = {
            name: (
                {
                    "status": "executed",
                    "metadata": executed_metadata,
                    "lean_report": executed_report,
                    "signals_count": 2,
                }
                if name == "baseline"
                else {"status": "not_testable", "error": "insufficient_training_distribution"}
            )
            for name in names
        }
        client._read_artifact = Mock(return_value={
            "report_type": "LEAN real historical strategy research lab",
            "synthetic_fixture": False,
            "market_data_type": "real_historical_ohlc",
            "asset_key": "GOLD",
            "timeframes": [{
                "metadata": {
                    "timeframe": "5m",
                    "market_data_source": "Dukascopy XAU/USD BID historical OHLC",
                    "raw_bars": 1200,
                },
                "results": results,
            }],
        })

        class FakeTelegram:
            def __init__(self):
                self.sent = []
            def send_long(self, text, chat_id):
                self.sent.append((text, chat_id))
                return True
            def send(self, text, chat_id):
                self.sent.append((text, chat_id))
                return True

        telegram = FakeTelegram()
        result, status = client.handle_callback(456, request_id, telegram)
        self.assertEqual(status, 200)
        self.assertTrue(result["ok"])
        self.assertEqual(len(telegram.sent), 1)
        self.assertIn("مختبر الاستراتيجيات", telegram.sent[0][0])
        client._read_artifact.assert_called_once_with(456, request_id, strategy_lab=True)
        self.assertEqual(db.get_setting("lean_active_request"), "")
        self.assertEqual(db.get_setting("lean_delivered:456"), "1")
        self.assertEqual(db.get_setting("lean_delivered:456:2"), "1")


if __name__ == "__main__":
    unittest.main()
