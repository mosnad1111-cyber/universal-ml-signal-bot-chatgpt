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


class FakeTelegram:
    def __init__(self):
        self.sent = []

    def send_long(self, text, chat_id):
        self.sent.append((text, chat_id))
        return True

    def send(self, text, chat_id):
        self.sent.append((text, chat_id))
        return True


class LeanActionsTests(unittest.TestCase):
    def make_client(self, db=None, session=None):
        with patch.dict(os.environ, {
            "GITHUB_ACTIONS_TOKEN": "test-token",
            "GITHUB_ACTIONS_REPOSITORY": "owner/repo",
            "LEAN_CALLBACK_BASE_URL": "https://bot.example.com",
        }, clear=False):
            return LeanActionsClient(db or FakeDB(), session=session or Mock())

    def test_dispatch_starts_workflow_dispatch_with_real_backtest_inputs(self):
        session = Mock()
        session.post.return_value = Mock(status_code=204, text="")
        client = self.make_client(session=session)
        client.dispatch("a" * 32, "BTC", ["5m", "1h"])
        args, kwargs = session.post.call_args
        self.assertIn("lean-real-backtest.yml/dispatches", args[0])
        self.assertEqual(kwargs["json"]["ref"], "main")
        self.assertEqual(kwargs["json"]["inputs"]["asset"], "BTC")
        self.assertEqual(kwargs["json"]["inputs"]["timeframes"], "5m,1h")

    def test_dispatch_rejects_invalid_asset(self):
        session = Mock()
        client = self.make_client(session=session)
        with self.assertRaises(LeanActionsError):
            client.dispatch("a" * 32, "FIXTURE", ["5m"])
        session.post.assert_not_called()

    def test_dispatch_requires_token_and_https_callback(self):
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
                },
            }],
        })
        self.assertIn("Dukascopy", report)
        self.assertIn("ليست حجم تداول حقيقي", report)
        self.assertIn("باكتيست تاريخي ببيانات سوق فعلية", report)
        self.assertIn("ليست عمولات وسيط فعلية", report)

    def _metric(self, trades=0):
        return {
            "trades": trades,
            "win_rate": 0.0,
            "net_r": 0.0,
            "net_after_cost_r": 0.0,
            "expectancy_after_cost_r": 0.0,
            "profit_factor": 0.0,
            "max_drawdown_r": 0.0,
        }

    def _candidate(self):
        candidate_id = "baseline__score_060_ai_030"
        side_metrics = {
            "all_gross": self._metric(),
            "calibration_gross": self._metric(),
            "holdout_gross": self._metric(),
            "all_net": self._metric(),
            "calibration_net": self._metric(),
            "holdout_net": self._metric(),
            "holdout_cost_005R": self._metric(),
            "holdout_cost_010R": self._metric(),
        }
        return {
            "candidate_id": candidate_id,
            "strategy_type": "baseline",
            "strategy_name": "الاستراتيجية الحالية",
            "threshold_id": "score_060_ai_030",
            "score_threshold": 60.0,
            "ai_threshold": 0.30,
            "is_live_threshold_pair": True,
            "is_live_baseline": True,
            "signals_count": 2,
            "setup_candidates": 2,
            "unfilled_setups": 0,
            "unresolved_trades": 0,
            "calibration_gross": self._metric(),
            "calibration_net": self._metric(),
            "holdout_gross": self._metric(),
            "holdout_net": self._metric(),
            "holdout_cost_005R": self._metric(),
            "holdout_cost_010R": self._metric(),
            "all_gross": self._metric(),
            "all_net": self._metric(),
            "directional": {"BUY": dict(side_metrics), "SELL": dict(side_metrics)},
            "lean_report": {
                "strategy": candidate_id, "signals": 2, "bars": 1200,
                "received_bars": 1200, "reader_parsed_rows": 1200,
                "reader_unique_timestamps": 1200, "order_errors": [],
            },
        }

    def test_unified_formatter_shows_strategy_threshold_and_buy_sell_results(self):
        candidate = self._candidate()
        summary = {
            "asset_key": "BTC",
            "engine": "QuantConnect LEAN open-source engine",
            "timeframes": [{
                "metadata": {
                    "timeframe": "1h",
                    "market_data_source": "Binance Spot REST BTC/USDT historical klines",
                    "raw_bars": 2000, "completed_bars": 2000, "usable_bars": 1900,
                    "snapshot_start": "2026-01-01T00:00:00+00:00",
                    "snapshot_end": "2026-02-01T00:00:00+00:00",
                    "midpoint": "2026-01-16T00:00:00+00:00",
                    "lean_initial_cash_usd": 150000,
                    "lean_cash_buffer_multiplier": 1.5,
                    "active_thresholds": {"score": 60.0, "ai": 0.30},
                },
                "scores": [60.0], "ai_probabilities": [0.30],
                "candidate_count_per_strategy": 1,
                "executed_candidates": 1, "expected_candidates": 4,
                "not_testable_candidates": 3,
                "live_baseline": candidate,
                "calibration_selected": candidate,
                "top_calibration_candidates": [candidate],
                "strategy_best": {
                    "baseline": candidate, "trend_pullback": None,
                    "breakout_retest": None, "momentum_continuation": None,
                },
                "directional_best": {"BUY": candidate, "SELL": candidate},
                "results": {
                    "baseline": {"executed_candidates": 1, "not_testable_candidates": 0},
                    "trend_pullback": {"executed_candidates": 0, "not_testable_candidates": 1},
                    "breakout_retest": {"executed_candidates": 0, "not_testable_candidates": 1},
                    "momentum_continuation": {"executed_candidates": 0, "not_testable_candidates": 1},
                },
            }],
        }
        report = LeanActionsClient.format_strategy_lab_report(summary)
        self.assertIn("Score", report)
        self.assertIn("AI", report)
        self.assertIn("BUY شراء", report)
        self.assertIn("SELL بيع", report)
        self.assertIn("Calibration", report)
        self.assertIn("Holdout", report)
        self.assertIn("المختبر الموحد", report)

    def test_dispatch_starts_unified_strategy_lab_workflow(self):
        session = Mock()
        session.post.return_value = Mock(status_code=204, text="")
        client = self.make_client(session=session)
        client.dispatch_strategy_lab("d" * 32, "GOLD", ["5m", "15m", "1h"])
        args, kwargs = session.post.call_args
        self.assertIn("lean-strategy-lab.yml/dispatches", args[0])
        self.assertEqual(kwargs["json"]["inputs"]["asset"], "GOLD")
        self.assertEqual(kwargs["json"]["inputs"]["timeframes"], "5m,15m,1h")

    def test_callback_accepts_only_verified_combined_strategy_threshold_artifact(self):
        request_id = "e" * 32
        db = FakeDB({
            "telegram_chat_id": "12345",
            "lean_active_request": request_id,
            "lean_delivered:456": "1",
            f"lean_request:{request_id}": json.dumps({
                "chat_id": "12345", "asset_key": "GOLD", "timeframes": ["5m"],
                "mode": "strategy_lab", "status": "dispatched",
            }),
        })
        client = self.make_client(db=db)
        client._get_json = Mock(side_effect=[
            {
                "path": ".github/workflows/lean-strategy-lab.yml",
                "event": "workflow_dispatch",
                "display_title": f"LEAN strategy x thresholds {request_id}",
                "head_branch": "main", "run_attempt": 2,
                "status": "in_progress", "conclusion": None,
                "html_url": "https://github.com/owner/repo/actions/runs/456",
            },
            {"jobs": [{"steps": [{"name": "Upload report", "conclusion": "success"}]}]},
        ])
        candidate = self._candidate()
        candidate_id = candidate["candidate_id"]
        names = ("baseline", "trend_pullback", "breakout_retest", "momentum_continuation")
        results = {}
        for strategy in names:
            if strategy == "baseline":
                results[strategy] = {
                    "status": "executed",
                    "candidate_order": [candidate_id],
                    "candidate_count": 1,
                    "executed_candidates": 1,
                    "not_testable_candidates": 0,
                    "candidates": {candidate_id: {"status": "executed", **candidate}},
                }
            else:
                other_id = f"{strategy}__score_060_ai_030"
                results[strategy] = {
                    "status": "not_testable",
                    "candidate_order": [other_id],
                    "candidate_count": 1,
                    "executed_candidates": 0,
                    "not_testable_candidates": 1,
                    "candidates": {other_id: {
                        "status": "not_testable", "candidate_id": other_id,
                        "error": "insufficient_training_distribution",
                    }},
                }
        client._read_artifact = Mock(return_value={
            "report_type": "LEAN real historical strategy-threshold lab",
            "synthetic_fixture": False,
            "market_data_type": "real_historical_ohlc",
            "asset_key": "GOLD",
            "strategy_order": list(names),
            "timeframes": [{
                "metadata": {
                    "timeframe": "5m",
                    "market_data_source": "Dukascopy XAU/USD BID historical OHLC",
                    "raw_bars": 1200, "bars_count": 1200,
                    "active_thresholds": {"score": 60, "ai": 0.30},
                },
                "scores": [60.0], "ai_probabilities": [0.30],
                "threshold_order": ["score_060_ai_030"],
                "expected_candidates": 4,
                "executed_candidates": 1,
                "not_testable_candidates": 3,
                "live_baseline": candidate,
                "calibration_selected": candidate,
                "top_calibration_candidates": [candidate],
                "strategy_best": {strategy: candidate if strategy == "baseline" else None for strategy in names},
                "directional_best": {"BUY": candidate, "SELL": candidate},
                "results": results,
            }],
        })

        telegram = FakeTelegram()
        result, status = client.handle_callback(456, request_id, telegram)
        self.assertEqual(status, 200)
        self.assertTrue(result["ok"])
        self.assertEqual(len(telegram.sent), 1)
        self.assertIn("المختبر الموحد", telegram.sent[0][0])
        self.assertEqual(telegram.sent[0][1], "12345")
        client._read_artifact.assert_called_once_with(456, request_id, strategy_lab=True)
        self.assertEqual(db.get_setting("lean_active_request"), "")
        self.assertEqual(db.get_setting("lean_delivered:456:2"), "1")

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


if __name__ == "__main__":
    unittest.main()
