import json
import os
import unittest
from unittest.mock import Mock, patch

from lean_actions import LeanActionsClient


class FakeDB:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def get_setting(self, key, default=None):
        return self.values.get(key, default)

    def set_setting(self, key, value):
        self.values[key] = str(value)


class FakeTelegram:
    def __init__(self):
        self.sent = []

    def send_long(self, text, chat_id):
        self.sent.append((text, chat_id))
        return True

    def send(self, text, chat_id):
        self.sent.append((text, chat_id))
        return True


class LeanThresholdActionTests(unittest.TestCase):
    def make_client(self, db=None, session=None):
        with patch.dict(os.environ, {
            "GITHUB_ACTIONS_TOKEN": "test-token",
            "GITHUB_ACTIONS_REPOSITORY": "owner/repo",
            "LEAN_CALLBACK_BASE_URL": "https://bot.example.com",
        }, clear=False):
            return LeanActionsClient(db or FakeDB(), session=session or Mock())

    def test_dispatch_starts_dedicated_threshold_workflow(self):
        session = Mock()
        session.post.return_value = Mock(status_code=204, text="")
        client = self.make_client(session=session)
        client.dispatch_threshold_test("a" * 32, "BTC", ["5m", "15m", "1h"])
        args, kwargs = session.post.call_args
        self.assertIn("lean-threshold-test.yml/dispatches", args[0])
        self.assertEqual(kwargs["json"]["ref"], "main")
        self.assertEqual(kwargs["json"]["inputs"]["asset"], "BTC")
        self.assertEqual(kwargs["json"]["inputs"]["timeframes"], "5m,15m,1h")
        self.assertEqual(
            kwargs["json"]["inputs"]["callback_url"],
            "https://bot.example.com/lean-backtest/callback",
        )

    def test_callback_accepts_only_verified_real_history_lean_threshold_artifact(self):
        request_id = "b" * 32
        candidate_id = "score_060_ai_030"
        pending_key = f"lean_request:{request_id}"
        db = FakeDB({
            "telegram_chat_id": "98765",
            "lean_active_request": request_id,
            pending_key: json.dumps({
                "chat_id": "98765",
                "asset_key": "GOLD",
                "timeframes": ["5m"],
                "mode": "threshold_test",
                "status": "dispatched",
            }),
        })
        client = self.make_client(db=db)
        client._get_json = Mock(side_effect=[
            {
                "path": ".github/workflows/lean-threshold-test.yml",
                "event": "workflow_dispatch",
                "display_title": f"LEAN threshold test {request_id}",
                "head_branch": "main",
                "run_attempt": 1,
                "status": "in_progress",
                "conclusion": None,
                "html_url": "https://github.com/owner/repo/actions/runs/456",
            },
            {"jobs": [{"steps": [{"name": "Upload report", "conclusion": "success"}]}]},
        ])
        metadata = {
            "strategy": candidate_id,
            "score_threshold": 60.0,
            "ai_threshold": 0.30,
            "is_live_baseline": True,
            "market_data_source": "Dukascopy XAU/USD BID historical OHLC",
            "raw_bars": 1200,
            "bars_count": 1200,
            "signals_count": 2,
        }
        report = {
            "strategy": candidate_id,
            "signals": 2,
            "bars": 1200,
            "received_bars": 1200,
            "reader_parsed_rows": 1200,
            "reader_unique_timestamps": 1200,
            "order_errors": [],
            "trades": 1,
            "unfilled_setups": 0,
            "unresolved_trades": 0,
        }
        candidate = {
            "threshold_id": candidate_id,
            "status": "executed",
            "metadata": metadata,
            "lean_report": report,
            "score_threshold": 60.0,
            "ai_threshold": 0.30,
            "is_live_baseline": True,
            "calibration_net": {
                "trades": 30, "net_after_cost_r": 1.2,
                "win_rate": 53.0, "profit_factor": 1.4,
            },
            "holdout_net": {
                "trades": 10, "net_after_cost_r": 0.4,
            },
            "holdout_cost_010R": {"net_after_cost_r": -0.1},
            "unfilled_setups": 2,
        }
        client._read_artifact = Mock(return_value={
            "report_type": "LEAN real historical threshold test",
            "synthetic_fixture": False,
            "market_data_type": "real_historical_ohlc",
            "asset_key": "GOLD",
            "timeframes": [{
                "metadata": {
                    "timeframe": "5m",
                    "market_data_source": "Dukascopy XAU/USD BID historical OHLC",
                    "raw_bars": 1200,
                },
                "active_thresholds": {"score": 60.0, "ai": 0.30},
                "live_baseline_threshold_id": candidate_id,
                "live_baseline": candidate,
                "calibration_selected": candidate,
                "top_calibration_candidates": [candidate],
                "threshold_results": [candidate],
                "executed_candidates": 1,
                "not_testable_candidates": 0,
            }],
        })
        telegram = FakeTelegram()
        result, status = client.handle_callback(456, request_id, telegram)
        self.assertEqual(status, 200)
        self.assertTrue(result["ok"])
        self.assertEqual(len(telegram.sent), 1)
        self.assertIn("اختبار عتبات AI / Score", telegram.sent[0][0])
        self.assertEqual(telegram.sent[0][1], "98765")
        self.assertEqual(db.get_setting("lean_active_request"), "")
        self.assertEqual(db.get_setting("lean_delivered:456"), "1")


if __name__ == "__main__":
    unittest.main()
