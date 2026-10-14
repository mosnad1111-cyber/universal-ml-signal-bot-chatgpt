import unittest
from unittest.mock import patch

import pandas as pd

from backtest import GoldBacktest


def _bars(index, trends):
    rows = []
    for trend in trends:
        if trend > 0:
            ema20, ema50, ema200 = 3.0, 2.0, 1.0
        else:
            ema20, ema50, ema200 = 1.0, 2.0, 3.0
        rows.append({
            "Open": 2.0, "High": 4.0, "Low": 1.0, "Close": 2.0,
            "Volume": 100.0, "ema20": ema20, "ema50": ema50,
            "ema200": ema200, "rsi": 55.0, "macd_hist": 0.1,
            "dist_ema20": 0.0,
        })
    return pd.DataFrame(rows, index=pd.DatetimeIndex(index))


class HigherTimeframeAvailabilityTests(unittest.TestCase):
    def test_context_only_appears_after_higher_timeframe_candle_closes(self):
        x_index = pd.to_datetime([
            "2026-10-14T10:00:00Z",
            "2026-10-14T10:05:00Z",
            "2026-10-14T10:15:00Z",
            "2026-10-14T11:00:00Z",
        ])
        x = pd.DataFrame({"Close": [1.0] * len(x_index)}, index=x_index)

        fifteen = _bars(
            pd.to_datetime([
                "2026-10-14T09:30:00Z",
                "2026-10-14T09:45:00Z",
                "2026-10-14T10:00:00Z",
                "2026-10-14T10:15:00Z",
            ]),
            [1, -1, 1, 1],
        )
        hourly = _bars(
            pd.to_datetime([
                "2026-10-14T09:00:00Z",
                "2026-10-14T10:00:00Z",
                "2026-10-14T11:00:00Z",
            ]),
            [-1, 1, 1],
        )

        def fake_fetch(_symbol, timeframe):
            return fifteen.copy() if timeframe == "15m" else hourly.copy()

        with patch("backtest.fetch", side_effect=fake_fetch), \
             patch("backtest.add_features", side_effect=lambda frame: frame.copy()):
            enriched = GoldBacktest()._enrich("5m", x)

        # The 09:45 15m candle closes at 10:00, so its bearish trend is usable.
        self.assertEqual(int(enriched.loc[x_index[0], "ctx15_trend"]), -1)
        # The 10:00 1h candle does not close until 11:00; do not leak its bullish trend.
        self.assertEqual(int(enriched.loc[x_index[1], "ctx1h_trend"]), -1)
        # Once the 10:00 1h candle has closed, its bullish trend becomes available.
        self.assertEqual(int(enriched.loc[x_index[3], "ctx1h_trend"]), 1)


if __name__ == "__main__":
    unittest.main()
