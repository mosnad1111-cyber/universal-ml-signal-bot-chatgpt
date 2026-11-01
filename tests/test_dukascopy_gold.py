import unittest
from unittest.mock import patch

import pandas as pd

import market


class DukascopyGoldNormalizationTests(unittest.TestCase):
    def test_normalizes_ohlc_to_utc_and_keeps_neutral_volume_proxy(self):
        index = pd.to_datetime([
            "2026-10-30T10:05:00Z",
            "2026-10-30T10:00:00Z",
            "2026-10-30T10:00:00Z",
        ])
        raw = pd.DataFrame(
            {
                "open": [2701.0, 2700.0, 2700.5],
                "high": [2702.0, 2701.0, 2701.5],
                "low": [2700.0, 2699.0, 2699.5],
                "close": [2701.5, 2700.5, 2701.0],
                "volume": [18.0, 12.0, 14.0],
            },
            index=index,
        )

        normalized = market._normalize_dukascopy_gold(raw)

        self.assertEqual(list(normalized.columns), ["Open", "High", "Low", "Close", "Volume"])
        self.assertEqual(len(normalized), 2)
        self.assertTrue(str(normalized.index.tz).upper() in ("UTC", "UTC+00:00"))
        self.assertTrue((normalized["Volume"] == 1.0).all())
        self.assertTrue(normalized.index.is_monotonic_increasing)
        self.assertFalse(normalized.index.has_duplicates)

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
