import pathlib
import unittest


class LeanRealWorkflowTests(unittest.TestCase):
    def test_real_workflow_does_not_call_synthetic_fixture(self):
        workflow = pathlib.Path(".github/workflows/lean-real-backtest.yml").read_text()
        self.assertIn("scripts/prepare_lean_backtest.py", workflow)
        self.assertIn("quantconnect/lean:latest", workflow)
        self.assertIn("type: string", workflow)
        self.assertIn("5m,15m,1h", workflow)
        self.assertNotIn("type: choice", workflow)
        self.assertIn("lean_smoke/LeanBacktestAlgorithm.py", workflow)
        self.assertIn("scripts/summarize_lean_backtest.py", workflow)
        self.assertIn("actions/upload-artifact@v4", workflow)
        self.assertIn("lean_smoke/create_lean_fixture.py", pathlib.Path(".github/workflows/lean-engine-smoke.yml").read_text())
        self.assertNotIn("create_lean_fixture.py", workflow)
        self.assertIn("market_data_type", pathlib.Path("scripts/summarize_lean_backtest.py").read_text())
        self.assertIn("synthetic_fixture", pathlib.Path("scripts/summarize_lean_backtest.py").read_text())


if __name__ == "__main__":
    unittest.main()
