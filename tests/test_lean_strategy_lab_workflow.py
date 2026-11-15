import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class LeanStrategyLabWorkflowTests(unittest.TestCase):
    def test_only_unified_lab_option_is_exposed_in_telegram(self):
        telegram = (ROOT / "telegram_ar.py").read_text(encoding="utf-8")
        self.assertIn("dispatch_strategy_lab", telegram)
        self.assertIn("'mode': 'strategy_lab'", telegram)
        self.assertIn("المختبر الموحد للاستراتيجيات والعتبات", telegram)
        self.assertNotIn("thresholds:GOLD", telegram)
        self.assertNotIn("thresholds:BTC", telegram)
        self.assertNotIn("def _grid_search", telegram)
        self.assertNotIn("StrategyResearchLab", telegram)

    def test_strategy_walk_forward_stream_is_reused_for_each_threshold_pair(self):
        preparation = (ROOT / "scripts/prepare_lean_strategy_lab.py").read_text(encoding="utf-8")
        self.assertIn("decision_only=True", preparation)
        self.assertIn("market_data_type", preparation)
        self.assertIn("signals_aligned_to_ohlc", preparation)
        self.assertIn("threshold_axes(tf)", preparation)
        self.assertIn("backtest._walk_forward_predictions", preparation)
        self.assertIn("_predictions=predictions", preparation)
        self.assertIn("min_score_override=score", preparation)
        self.assertIn("min_ai_override=ai", preparation)
        self.assertIn("strategy=strategy", preparation)

    def test_workflow_executes_every_strategy_threshold_pair_in_official_lean(self):
        workflow = (ROOT / ".github/workflows/lean-strategy-lab.yml").read_text(encoding="utf-8")
        self.assertIn("quantconnect/lean:latest", workflow)
        self.assertIn("Execute every strategy-threshold candidate inside LEAN", workflow)
        self.assertIn("trend_pullback", workflow)
        self.assertIn("breakout_retest", workflow)
        self.assertIn("momentum_continuation", workflow)
        self.assertIn("prepare_lean_strategy_lab.py", workflow)
        self.assertIn("summarize_lean_strategy_lab.py", workflow)
        self.assertIn("strategies/$strategy/candidates", workflow)
        self.assertIn("results/$tf/$strategy/$candidate_id", workflow)
        self.assertIn("lean_runtime/LeanBacktestAlgorithm.py", workflow)
        self.assertIn("lean-strategy-lab-" + "$" + "{{ inputs.request_id }}", workflow)
        self.assertNotIn("create_lean_fixture.py", workflow)
        self.assertFalse((ROOT / ".github/workflows/lean-threshold-test.yml").exists())

    def test_workflow_expands_github_expressions_and_shell_candidate_ids(self):
        workflow = (ROOT / ".github/workflows/lean-strategy-lab.yml").read_text(encoding="utf-8")
        self.assertNotIn("\\${{", workflow)
        self.assertNotIn("\\${strategy}", workflow)
        self.assertNotIn("\\${threshold_id}", workflow)
        self.assertIn("OUTPUT_DIR: ${{ runner.temp }}/lean-lab/input", workflow)
        self.assertIn("REQUEST_ID: ${{ inputs.request_id }}", workflow)
        self.assertIn("PREPARED_DIR: ${{ runner.temp }}/lean-lab/input", workflow)
        self.assertIn('candidate_id="${strategy}__${threshold_id}"', workflow)

    def test_btc_paper_trial_is_isolated_from_live_signal_pipeline(self):
        engine = (ROOT / "engine.py").read_text(encoding="utf-8")
        database = (ROOT / "db.py").read_text(encoding="utf-8")
        telegram = (ROOT / "telegram_ar.py").read_text(encoding="utf-8")
        self.assertIn("self.paper_ai = GoldAI()", engine)
        self.assertIn("strategy='trend_pullback'", engine)
        self.assertIn("PAPER_MIN_SCORE", engine)
        self.assertIn("PAPER_MIN_AI_PROB", engine)
        self.assertIn("self.db.paper_create(setup)", engine)
        self.assertIn("target=self.paper_scanner", engine)
        self.assertIn("CREATE TABLE IF NOT EXISTS paper_trades", database)
        self.assertIn("def paper_create", database)
        self.assertIn("paper:report", telegram)
        self.assertIn("self.ai = {a['symbol']: GoldAI() for a in ASSETS.values()}", engine)

    def test_research_history_uses_the_extended_configurable_targets(self):
        config = (ROOT / "config.py").read_text(encoding="utf-8")
        preparation = (ROOT / "scripts/prepare_lean_strategy_lab.py").read_text(encoding="utf-8")
        self.assertIn("STRATEGY_LAB_BARS_5M", config)
        self.assertIn("STRATEGY_LAB_BARS_15M", config)
        self.assertIn("STRATEGY_LAB_BARS_1H", config)
        self.assertIn("PAPER_MODEL_HISTORY_BARS", config)
        self.assertIn("max(STRATEGY_LAB_BARS_1H, BACKTEST_BARS_1H)", preparation)

    def test_lean_report_records_candidate_identity_and_fixed_unit_execution(self):
        algorithm = (ROOT / "lean_runtime/LeanBacktestAlgorithm.py").read_text(encoding="utf-8")
        self.assertIn('"strategy": self.metadata.get("strategy", "baseline")', algorithm)
        self.assertIn('self.set_cash(self.initial_cash_usd)', algorithm)
        self.assertIn('max_ohlc_price = float(self.metadata.get("max_ohlc_price", 0.0) or 0.0)', algorithm)
        self.assertIn('os.path.join(INPUT_DIR, "bars.csv")', algorithm)
        self.assertIn('"initial_cash_usd": float(self.initial_cash_usd)', algorithm)


if __name__ == "__main__":
    unittest.main()
