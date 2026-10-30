import sys
import types
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import ai_engine


class FakeXGBClassifier:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.classes_seen = None

    def fit(self, x, y, **kwargs):
        self.classes_seen = set(np.asarray(y).tolist())
        return self

    def predict_proba(self, x):
        return np.tile([0.5, 0.5], (len(x), 1))


class ValidationDistributionTests(unittest.TestCase):
    def test_single_class_validation_fold_does_not_block_training(self):
        index = pd.date_range("2025-01-01", periods=1000, freq="5min", tz="UTC")
        rng = np.random.default_rng(17)
        frame = pd.DataFrame(
            {name: rng.normal(size=len(index)) for name in ai_engine.FEATURES[:-1]},
            index=index,
        )

        def labels_for_direction(x, direction, *args, **kwargs):
            if direction == 0:
                return pd.Series(np.nan, index=x.index)
            values = np.tile([0.0, 1.0], len(x) // 2)
            if len(values) < len(x):
                values = np.append(values, 0.0)
            # The validation tail has only one class, which is valid for a
            # chronological fold; the training prefix still contains both.
            values[800:] = 0.0
            return pd.Series(values, index=x.index)

        fake_xgboost = types.SimpleNamespace(XGBClassifier=FakeXGBClassifier)
        model = ai_engine.GoldAI()
        with patch.object(ai_engine, "_label_direction", side_effect=labels_for_direction), patch.dict(
            sys.modules, {"xgboost": fake_xgboost}
        ):
            ok = model.fit("5m", frame, fast_mode=True)

        self.assertTrue(ok, model.errors.get("5m"))
        self.assertIn("5m", model.models)
        self.assertNotIn("invalid_class_distribution", str(model.errors.get("5m", "")))


if __name__ == "__main__":
    unittest.main()
