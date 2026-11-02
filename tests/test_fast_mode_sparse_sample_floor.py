import sys
import types
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import ai_engine


class FakeXGBClassifier:
    def __init__(self, **kwargs):
        self.classes_seen = None

    def fit(self, x, y, **kwargs):
        self.classes_seen = set(np.asarray(y).tolist())
        return self

    def predict_proba(self, x):
        return np.tile([0.5, 0.5], (len(x), 1))


class FastModeSparseSampleFloorTests(unittest.TestCase):
    def setUp(self):
        self.index = pd.date_range("2026-01-01", periods=180, freq="5min", tz="UTC")
        rng = np.random.default_rng(73)
        self.frame = pd.DataFrame(
            {name: rng.normal(size=len(self.index)) for name in ai_engine.FEATURES[:-1]},
            index=self.index,
        )
        self.fake_xgboost = types.SimpleNamespace(XGBClassifier=FakeXGBClassifier)

    def _fit_with_labels(self, row_count, positive_count):
        def labels_for_direction(x, direction, *args, **kwargs):
            if direction == 0:
                return pd.Series(np.nan, index=x.index)
            values = np.full(len(x), np.nan)
            values[:row_count] = 0.0
            values[:positive_count] = 1.0
            return pd.Series(values, index=x.index)

        model = ai_engine.GoldAI()
        with patch.object(ai_engine, "_label_direction", side_effect=labels_for_direction), patch.dict(
            sys.modules, {"xgboost": self.fake_xgboost}
        ):
            ok = model.fit("5m", self.frame, fast_mode=True, strategy="breakout_retest")
        return model, ok

    def test_balanced_114_row_fold_trains(self):
        model, ok = self._fit_with_labels(114, 43)
        self.assertTrue(ok, model.errors.get("5m"))
        self.assertEqual(model.metrics["5m"]["training_rows"], 114)
        self.assertEqual(model.metrics["5m"]["training_candles"], 114)
        self.assertEqual(model.metrics["5m"]["training_class_counts"], {"1": 43, "0": 71})

    def test_fold_below_100_rows_is_rejected(self):
        model, ok = self._fit_with_labels(99, 40)
        self.assertFalse(ok)
        self.assertIn("rows=99", model.errors["5m"])

    def test_fold_with_fewer_than_30_rows_in_a_class_is_rejected(self):
        model, ok = self._fit_with_labels(114, 29)
        self.assertFalse(ok)
        self.assertIn("min_class_rows=29", model.errors["5m"])


if __name__ == "__main__":
    unittest.main()
