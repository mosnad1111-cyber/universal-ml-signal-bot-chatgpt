import unittest
import numpy as np
import pandas as pd

from ai_engine import _label_direction


class TradeLabelAlignmentTests(unittest.TestCase):
    def _frame(self, future_rows):
        # First candle produces a buy entry at 95.0 with risk exactly 3 ATR.
        rows = [{
            "Close": 95.0, "High": 96.0, "Low": 94.0,
            "atr": 2.0, "high20": 100.0, "low20": 90.0,
        }]
        rows.extend(future_rows)
        return pd.DataFrame(rows)

    def test_buy_label_waits_for_entry_then_target(self):
        x = self._frame([
            {"Close": 96, "High": 97, "Low": 95, "atr": 2, "high20": 100, "low20": 90},
            {"Close": 106, "High": 108, "Low": 96, "atr": 2, "high20": 100, "low20": 90},
            {"Close": 106, "High": 107, "Low": 105, "atr": 2, "high20": 100, "low20": 90},
        ])
        y = _label_direction(x, 1, horizon=3, rr=2.0, divisor=3.6, sl_atr_mult=1.15)
        self.assertEqual(y.iloc[0], 1.0)

    def test_buy_label_is_loss_when_stop_is_hit_after_entry(self):
        x = self._frame([
            {"Close": 95, "High": 96, "Low": 94, "atr": 2, "high20": 100, "low20": 90},
            {"Close": 90, "High": 96, "Low": 89, "atr": 2, "high20": 100, "low20": 90},
            {"Close": 90, "High": 92, "Low": 89, "atr": 2, "high20": 100, "low20": 90},
        ])
        y = _label_direction(x, 1, horizon=3, rr=2.0, divisor=3.6, sl_atr_mult=1.15)
        self.assertEqual(y.iloc[0], 0.0)

    def test_no_entry_touch_means_unresolved_label(self):
        x = self._frame([
            {"Close": 92, "High": 94, "Low": 91, "atr": 2, "high20": 100, "low20": 90},
            {"Close": 93, "High": 94, "Low": 92, "atr": 2, "high20": 100, "low20": 90},
            {"Close": 93, "High": 94, "Low": 92, "atr": 2, "high20": 100, "low20": 90},
        ])
        y = _label_direction(x, 1, horizon=3, rr=2.0, divisor=3.6, sl_atr_mult=1.15)
        self.assertTrue(np.isnan(y.iloc[0]))


if __name__ == "__main__":
    unittest.main()
