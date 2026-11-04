import pathlib
import unittest


class LeanRealWorkflowTests(unittest.TestCase):
    def test_real_workflow_does_not_call_synthetic_fixture(self):
        workflow = pathlib.Path(".github/workflows/lean-real-backtest.yml").read_text()
        self.assertIn("scripts/prepare_lean_backtest.py", workflow)
        preparation_step = workflow.split("      - name: Validate request and prepare real historical market candles", 1)[1].split("      - name:", 1)[0]
        self.assertIn("PYTHONPATH: ${{ github.workspace }}", preparation_step)
        self.assertIn("quantconnect/lean:latest", workflow)
        self.assertIn("type: string", workflow)
        self.assertIn("5m,15m,1h", workflow)
        self.assertEqual(workflow.count("type: choice"), 1)  # asset selector only
        self.assertIn("TF_LIST[@]", workflow)
        self.assertIn("IFS=", workflow)
        self.assertIn('cp -a "$LEAN_DIR/input/$tf/." "$LEAN_DIR/Algorithm.Python/input/"', workflow)
        self.assertNotIn('--volume "$LEAN_DIR/input/$tf:/Lean/Algorithm.Python/input:ro"', workflow)
        self.assertIn("sleep 300", workflow)
        self.assertIn("/health", workflow)
        self.assertIn("lean_smoke/LeanBacktestAlgorithm.py", workflow)
        lean_algorithm = pathlib.Path("lean_smoke/LeanBacktestAlgorithm.py").read_text()
        self.assertIn("Resolution.TICK", lean_algorithm)
        self.assertIn('"reader_parsed_rows"', lean_algorithm)
        self.assertIn('"reader_unique_timestamps"', lean_algorithm)
        self.assertIn('datetime.fromisoformat(fields[0])', lean_algorithm)
        self.assertIn('INPUT_DIR, "bars.csv"', lean_algorithm)
        self.assertIn('"reader_daily_counts"', lean_algorithm)
        self.assertNotIn('INPUT_DIR, "bars", date.strftime', lean_algorithm)
        self.assertIn('timespec="microseconds"', pathlib.Path("scripts/prepare_lean_backtest.py").read_text())
        self.assertIn("scripts/summarize_lean_backtest.py", workflow)
        self.assertIn("actions/upload-artifact@v4", workflow)
        self.assertIn("lean_smoke/create_lean_fixture.py", pathlib.Path(".github/workflows/lean-engine-smoke.yml").read_text())
        fixture = pathlib.Path("lean_smoke/create_lean_fixture.py").read_text()
        self.assertIn('"bars.csv"', fixture)
        self.assertNotIn("bars_dir", fixture)
        self.assertNotIn("create_lean_fixture.py", workflow)
        self.assertIn("market_data_type", pathlib.Path("scripts/summarize_lean_backtest.py").read_text())
        self.assertIn("synthetic_fixture", pathlib.Path("scripts/summarize_lean_backtest.py").read_text())
        preparation = pathlib.Path("scripts/prepare_lean_backtest.py").read_text()
        summary = pathlib.Path("scripts/summarize_lean_backtest.py").read_text()
        self.assertIn("reader_unique_timestamps", summary)
        self.assertIn("reader_parsed_rows", summary)
        self.assertIn("emitted", summary)
        self.assertIn("daily_bar_counts", preparation)
        self.assertIn("daily_bar_counts", summary)
        self.assertIn("Upload LEAN diagnostics even when verification fails", workflow)
        preparation = pathlib.Path("scripts/prepare_lean_backtest.py").read_text()
        for field in ("requested_bars", "raw_bars", "completed_bars", "usable_bars", "history_partial"):
            self.assertIn(f'"{field}"', preparation)


if __name__ == "__main__":
    unittest.main()
