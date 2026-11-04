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
        self.assertIn("sleep 300", workflow)
        self.assertIn("/health", workflow)
        self.assertIn("lean_smoke/LeanBacktestAlgorithm.py", workflow)
        self.assertIn("scripts/summarize_lean_backtest.py", workflow)
        self.assertIn("actions/upload-artifact@v4", workflow)
        self.assertIn("lean_smoke/create_lean_fixture.py", pathlib.Path(".github/workflows/lean-engine-smoke.yml").read_text())
        self.assertNotIn("create_lean_fixture.py", workflow)
        self.assertIn("market_data_type", pathlib.Path("scripts/summarize_lean_backtest.py").read_text())
        self.assertIn("synthetic_fixture", pathlib.Path("scripts/summarize_lean_backtest.py").read_text())
        preparation = pathlib.Path("scripts/prepare_lean_backtest.py").read_text()
        for field in ("requested_bars", "raw_bars", "completed_bars", "usable_bars", "history_partial"):
            self.assertIn(f'"{field}"', preparation)


if __name__ == "__main__":
    unittest.main()
