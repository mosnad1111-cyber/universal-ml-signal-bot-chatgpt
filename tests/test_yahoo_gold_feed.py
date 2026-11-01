import unittest
import math

import pandas as pd

import market


class GoldVolumeFeatureTests(unittest.TestCase):
    def test_neutral_gold_volume_proxy_preserves_feature_rows(self):
        index = pd.date_range("2026-01-01", periods=500, freq="5min", tz="UTC")
        close = 2000 + pd.Series(range(500), dtype=float).apply(lambda i: 2 * math.sin(i / 8))
        frame = pd.DataFrame({
            "Open": close.shift(1).fillna(close.iloc[0]).to_numpy(),
            "High": (close + 1).to_numpy(),
            "Low": (close - 1).to_numpy(),
            "Close": close.to_numpy(),
            "Volume": 1.0,
        }, index=index)
        features = market.add_features(frame).dropna()
        self.assertGreater(len(features), 250)
        self.assertTrue((features["vol_ratio"] == 1.0).all())


if __name__ == "__main__":
    unittest.main()
