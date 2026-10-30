import unittest
from unittest.mock import patch

import pandas as pd

import backtest


class FakeAI:
    fit_calls = 0
    first_fit_succeeds = False

    def __init__(self):
        self.models = {}
        self.errors = {}

    def fit(self, timeframe, *args, **kwargs):
        type(self).fit_calls += 1
        if type(self).first_fit_succeeds and type(self).fit_calls == 1:
            self.models[timeframe] = object()
            return True
        self.errors[timeframe] = "insufficient_training_rows:42"
        return False

    def probability(self, timeframe, row, direction):
        return 0.7 if timeframe in self.models else None


class WalkForwardFitFailureTests(unittest.TestCase):
    def setUp(self):
        FakeAI.fit_calls = 0
        FakeAI.first_fit_succeeds = False
        backtest._PREDICTION_CACHE.clear()
        self.frame = pd.DataFrame(
            {"Close": [100.0] * 1000},
            index=pd.date_range("2026-01-01", periods=1000, freq="5min", tz="UTC"),
        )

    def test_failed_fits_are_reported_without_fake_probability_rows(self):
        with patch.object(backtest, "GoldAI", FakeAI):
            predictions = backtest.GoldBacktest()._walk_forward_predictions(
                "5m", self.frame, retrain_every=200, strategy="breakout_retest"
            )

        diagnostics = predictions["_diagnostics"]
        self.assertEqual(diagnostics["fit_failures"], 2)
        self.assertEqual(diagnostics["last_fit_error"], "insufficient_training_rows:42")
        self.assertEqual(diagnostics["model_unavailable_candles"], 299)
        self.assertFalse(any(isinstance(key, int) for key in predictions))

    def test_failed_refit_keeps_last_successful_model(self):
        FakeAI.first_fit_succeeds = True
        with patch.object(backtest, "GoldAI", FakeAI):
            predictions = backtest.GoldBacktest()._walk_forward_predictions(
                "5m", self.frame, retrain_every=200, strategy="breakout_retest"
            )

        diagnostics = predictions["_diagnostics"]
        self.assertEqual(diagnostics["fit_failures"], 1)
        self.assertIn(900, predictions)
        self.assertEqual(predictions[900]["BUY"], 0.7)
        self.assertEqual(predictions[900]["SELL"], 0.7)


if __name__ == "__main__":
    unittest.main()
