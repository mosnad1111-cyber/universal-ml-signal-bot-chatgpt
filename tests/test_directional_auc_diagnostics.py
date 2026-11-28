import sys
import types
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import ai_engine


class DirectionAwareFakeXGB:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def fit(self, x, y, **kwargs):
        return self

    def predict_proba(self, x):
        direction = x["direction"].to_numpy(dtype=float)
        is_positive_feature = (x["ema9"].to_numpy(dtype=float) > 0).astype(float)
        # Each direction ranks its own positive/negative outcomes perfectly,
        # but their probability ranges have a direction-level offset.
        probability = np.where(
            direction == 1,
            0.10 + 0.35 * is_positive_feature,
            0.65 + 0.30 * is_positive_feature,
        )
        probability = np.clip(probability, 0.0, 1.0)
        return np.column_stack([1.0 - probability, probability])


class DirectionalAucDiagnosticsTests(unittest.TestCase):
    def test_macro_auc_reflects_within_direction_quality_not_cross_side_offsets(self):
        index = pd.date_range("2025-01-01", periods=1000, freq="5min", tz="UTC")
        frame = pd.DataFrame(
            {name: np.zeros(len(index), dtype=float) for name in ai_engine.FEATURES[:-1]},
            index=index,
        )
        signal = np.tile([-1.0, 1.0], len(index) // 2)
        frame["ema9"] = signal

        def labels_for_direction(x, direction, *args, **kwargs):
            labels = (x["ema9"] > 0).astype(float)
            return pd.Series(labels.to_numpy(), index=x.index)

        model = ai_engine.GoldAI()
        fake_xgboost = types.SimpleNamespace(XGBClassifier=DirectionAwareFakeXGB)
        with patch.object(ai_engine, "_label_direction", side_effect=labels_for_direction), patch.dict(
            sys.modules, {"xgboost": fake_xgboost}
        ):
            ok = model.fit("5m", frame)

        self.assertTrue(ok, model.errors.get("5m"))
        metrics = model.info()["5m"]
        self.assertAlmostEqual(metrics["buy_auc"], 1.0)
        self.assertAlmostEqual(metrics["sell_auc"], 1.0)
        self.assertAlmostEqual(metrics["directional_macro_auc"], 1.0)
        self.assertLess(metrics["pooled_auc"], metrics["directional_macro_auc"])
        self.assertIn("pooled_across_buy_sell", metrics["auc_scope"])
        self.assertIsNotNone(metrics["buy_brier_skill"])
        self.assertIsNotNone(metrics["sell_brier_skill"])


if __name__ == "__main__":
    unittest.main()
