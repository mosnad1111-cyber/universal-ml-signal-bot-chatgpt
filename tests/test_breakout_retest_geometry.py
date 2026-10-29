import unittest
import pandas as pd

from decision import build_setup


class BreakoutRetestGeometryTests(unittest.TestCase):
    def test_breakout_retest_uses_atr_bounded_stop(self):
        index = pd.date_range("2026-01-01", periods=221, freq="5min", tz="UTC")
        df = pd.DataFrame({
            "Open": [100.0] * 221,
            "High": [101.0] * 221,
            "Low": [99.0] * 221,
            "Close": [100.0] * 221,
            "atr": [1.0] * 221,
            "ema20": [101.0] * 221,
            "ema50": [100.0] * 221,
            "ema200": [99.0] * 221,
            "rsi": [58.0] * 221,
            "macd_hist": [0.1] * 221,
            "high20": [100.0] * 221,
            "low20": [98.0] * 221,
            "breakout_up": [True] * 221,
            "breakout_dn": [False] * 221,
            "vol_ratio": [1.2] * 221,
        }, index=index)
        df.iloc[-2, df.columns.get_loc("Close")] = 101.0
        df.iloc[-2, df.columns.get_loc("High")] = 101.5
        df.iloc[-2, df.columns.get_loc("Low")] = 99.5

        setup = build_setup(
            df, {"BUY": 0.9, "SELL": 0.1},
            min_score=50, min_ai=0.5, min_gap=0,
            context={"trend": 1}, strategy="breakout_retest",
        )
        self.assertIsNotNone(setup)
        self.assertAlmostEqual(setup["entry"], 100.0)
        self.assertAlmostEqual(setup["sl"], 98.85)
        self.assertAlmostEqual(setup["risk"], 1.15)
        self.assertLessEqual(setup["risk"] / 1.0, 3.0)


if __name__ == "__main__":
    unittest.main()
