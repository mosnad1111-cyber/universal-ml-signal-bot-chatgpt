import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class LeanStrategyLabWorkflowTests(unittest.TestCase):
    def test_telegram_lab_button_dispatches_to_lean_instead_of_python_trade_simulator(self):
        telegram = (ROOT / "telegram_ar.py").read_text(encoding="utf-8")
        self.assertIn("dispatch_strategy_lab", telegram)
        self.assertIn("'mode': 'strategy_lab'", telegram)
        self.assertNotIn("StrategyResearchLab", telegram)

    def test_decision_preparation_is_separate_from_trade_resolution(self):
        backtest = (ROOT / "backtest.py").read_text(encoding="utf-8")
        self.assertIn("decision_only=False", backtest)
        self.assertIn("if decision_only:", backtest)
        self.assertLess(backtest.index("if decision_only:"), backtest.index("self._resolve(raw, start, setup)"))
        self.assertIn("lean_events.append", backtest)

        preparation = (ROOT / "scripts/prepare_lean_strategy_lab.py").read_text(encoding="utf-8")
        self.assertIn("decision_only=True", preparation)
        self.assertIn("market_data_type", preparation)
        self.assertIn("signals_aligned_to_ohlc", preparation)

    def test_workflow_replays_each_strategy_through_official_lean_engine(self):
        workflow = (ROOT / ".github/workflows/lean-strategy-lab.yml").read_text(encoding="utf-8")
        self.assertIn("quantconnect/lean:latest", workflow)
        self.assertIn("Execute every eligible strategy inside LEAN", workflow)
        self.assertIn("trend_pullback", workflow)
        self.assertIn("breakout_retest", workflow)
        self.assertIn("momentum_continuation", workflow)
        self.assertIn("prepare_lean_strategy_lab.py", workflow)
        self.assertIn("summarize_lean_strategy_lab.py", workflow)
        self.assertIn("lean-strategy-lab-" + "$" + "{{ inputs.request_id }}", workflow)
        self.assertNotIn("create_lean_fixture.py", workflow)

    def test_lean_report_records_the_strategy_that_was_executed(self):
        algorithm = (ROOT / "lean_smoke/LeanBacktestAlgorithm.py").read_text(encoding="utf-8")
        self.assertIn('"strategy": self.metadata.get("strategy", "baseline")', algorithm)


if __name__ == "__main__":
    unittest.main()
