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


class SparseStrategyTrainingTests(unittest.TestCase):
    def setUp(self):
        self.index = pd.date_range("2025-01-01", periods=500, freq="5min", tz="UTC")
        rng = np.random.default_rng(41)
        self.frame = pd.DataFrame(
            {name: rng.normal(size=len(self.index)) for name in ai_engine.FEATURES[:-1]},
            index=self.index,
        )
        self.fake_xgboost = types.SimpleNamespace(XGBClassifier=FakeXGBClassifier)

    def test_purged_strategy_fold_with_364_train_rows_can_fit(self):
        def labels_for_direction(x, direction, *args, **kwargs):
            if direction == 0:
                return pd.Series(np.nan, index=x.index)
            values = np.tile([0.0, 1.0], len(x) // 2)
            if len(values) < len(x):
                values = np.append(values, 0.0)
            return pd.Series(values, index=x.index)

        model = ai_engine.GoldAI()
        with patch.object(ai_engine, "_label_direction", side_effect=labels_for_direction), patch.dict(
            sys.modules, {"xgboost": self.fake_xgboost}
        ):
            ok = model.fit("5m", self.frame, fast_mode=True, strategy="breakout_retest")

        self.assertTrue(ok, model.errors.get("5m"))
        self.assertIn("5m", model.models)
        self.assertGreaterEqual(model.metrics["5m"]["training_rows"], 400)

    def test_training_rejects_extremely_imbalanced_training_classes(self):
        def labels_for_direction(x, direction, *args, **kwargs):
            if direction == 0:
                return pd.Series(np.nan, index=x.index)
            values = np.zeros(len(x), dtype=float)
            values[0] = 1.0
            return pd.Series(values, index=x.index)

        model = ai_engine.GoldAI()
        with patch.object(ai_engine, "_label_direction", side_effect=labels_for_direction), patch.dict(
            sys.modules, {"xgboost": self.fake_xgboost}
        ):
            ok = model.fit("5m", self.frame, fast_mode=True, strategy="breakout_retest")

        self.assertFalse(ok)
        self.assertIn("min_class_rows=", model.errors["5m"])


if __name__ == "__main__":
    unittest.main()
