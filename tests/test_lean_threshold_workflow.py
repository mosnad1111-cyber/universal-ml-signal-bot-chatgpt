import unittest
from pathlib import Path


class LeanThresholdWorkflowTests(unittest.TestCase):
    def test_threshold_workflow_uses_real_history_and_replays_candidates_in_lean(self):
        workflow = Path(".github/workflows/lean-threshold-test.yml").read_text(encoding="utf-8")
        self.assertIn("prepare_lean_threshold_test.py", workflow)
        self.assertIn("summarize_lean_threshold_test.py", workflow)
        self.assertIn("quantconnect/lean:latest", workflow)
        self.assertIn('"$PREPARED_DIR/$tf/thresholds"/*', workflow)
        self.assertIn('lean-threshold-test-\${{ inputs.request_id }}', workflow)
        self.assertIn("REAL_HISTORICAL_THRESHOLD_TEST_INPUTS_VERIFIED", workflow)
        self.assertIn("synthetic_fixture", workflow)
        self.assertIn("/health", workflow)
        self.assertIn("Build verified LEAN threshold comparison report", workflow)
        self.assertNotIn("lean_smoke", workflow)


if __name__ == "__main__":
    unittest.main()
