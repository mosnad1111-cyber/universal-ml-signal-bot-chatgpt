import unittest
from unittest.mock import patch
from types import SimpleNamespace

import pandas as pd

import market


class DukascopyGoldNormalizationTests(unittest.TestCase):
    def test_normalizes_ohlc_to_utc_and_keeps_neutral_volume_proxy(self):
        index = pd.date_range("2026-10-30T10:00:00Z", periods=250, freq="5min")
        index = index.append(pd.DatetimeIndex([index[0]]))
        values = list(range(250)) + [999]
        raw = pd.DataFrame(
            {
                "open": [2700.0 + value / 100 for value in values],
                "high": [2701.0 + value / 100 for value in values],
                "low": [2699.0 + value / 100 for value in values],
                "close": [2700.5 + value / 100 for value in values],
                "volume": [10.0] * len(values),
            },
            index=index,
        )

        normalized = market._normalize_dukascopy_gold(raw)

        self.assertEqual(list(normalized.columns), ["Open", "High", "Low", "Close", "Volume"])
        self.assertEqual(len(normalized), 250)
        self.assertEqual(str(normalized.index.tz), "UTC")
        self.assertTrue((normalized["Volume"] == 1.0).all())
        self.assertTrue(normalized.index.is_monotonic_increasing)
        self.assertFalse(normalized.index.has_duplicates)

    def test_short_month_chunk_is_normalized_without_minimum_history_rejection(self):
        raw = pd.DataFrame(
            {"open": [2700.0] * 20, "high": [2701.0] * 20,
             "low": [2699.0] * 20, "close": [2700.5] * 20},
            index=pd.date_range("2026-10-01", periods=20, freq="1h", tz="UTC"),
        )
        normalized = market._normalize_dukascopy_gold(raw)
        self.assertEqual(len(normalized), 20)
        self.assertTrue((normalized["Volume"] == 1.0).all())


    def test_recent_refresh_range_accepts_small_incremental_window(self):
        raw = pd.DataFrame(
            {
                "open": [2700.0] * 30,
                "high": [2701.0] * 30,
                "low": [2699.0] * 30,
                "close": [2700.5] * 30,
            },
            index=pd.date_range("2026-10-01", periods=30, freq="15min", tz="UTC"),
        )
        with patch.object(market, "_dukascopy_interval", return_value=("xau", "15m", "bid")), \
             patch.object(market, "_duka", SimpleNamespace(fetch=lambda *args: raw)):
            result = market._fetch_dukascopy_range(
                "15m",
                pd.Timestamp("2026-10-01T00:00:00Z"),
                pd.Timestamp("2026-10-01T08:00:00Z"),
            )
        self.assertEqual(len(result), 30)
        self.assertTrue((result["Volume"] == 1.0).all())

    def test_gold_fetch_uses_dukascopy_adapter(self):
        frame = pd.DataFrame(
            {
                "Open": [2700.0] * 300,
                "High": [2701.0] * 300,
                "Low": [2699.0] * 300,
                "Close": [2700.5] * 300,
                "Volume": [1.0] * 300,
            },
            index=pd.date_range("2026-10-01", periods=300, freq="5min", tz="UTC"),
        )
        market._cache.clear()
        with patch.object(market, "_fetch_dukascopy_gold", return_value=frame) as provider:
            result = market.fetch("TVC:GOLD", "5m", period=300)
        provider.assert_called_once_with("5m", 300)
        self.assertEqual(len(result), 300)
        market._cache.clear()


if __name__ == "__main__":
    unittest.main()
