import unittest
from unittest.mock import patch
import pandas as pd
import market


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeRequests:
    calls = []

    @staticmethod
    def get(*args, **kwargs):
        FakeRequests.calls.append((args, kwargs))
        return FakeRequests.response


class TwelveDataGoldFeedTests(unittest.TestCase):
    def setUp(self):
        FakeRequests.calls = []

    def test_fetches_xauusd_spot_and_normalizes_candles(self):
        timestamps = pd.date_range("2026-01-01", periods=300, freq="5min", tz="UTC")
        values = []
        for i, timestamp in enumerate(timestamps[::-1]):
            price = 2000.0 + i
            values.append({
                "datetime": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "open": str(price),
                "high": str(price + 2),
                "low": str(price - 2),
                "close": str(price + 1),
            })
        FakeRequests.response = FakeResponse({
            "meta": {"symbol": "XAU/USD", "type": "Commodity"},
            "values": values,
            "status": "ok",
        })
        with patch.object(market, "requests", FakeRequests), patch.dict("os.environ", {"TWELVEDATA_API_KEY": "test-key"}):
            frame = market._fetch_twelvedata_gold("5m", 300)

        self.assertEqual(len(frame), 300)
        self.assertEqual(list(frame.columns), ["Open", "High", "Low", "Close", "Volume"])
        self.assertIsInstance(frame.index, pd.DatetimeIndex)
        self.assertEqual(str(frame.index.tz), "UTC")
        self.assertTrue(frame.index.is_monotonic_increasing)
        args, kwargs = FakeRequests.calls[0]
        self.assertEqual(args[0], "https://api.twelvedata.com/time_series")
        self.assertEqual(kwargs["params"]["symbol"], "XAU/USD")
        self.assertEqual(kwargs["params"]["interval"], "5min")
        self.assertEqual(kwargs["params"]["outputsize"], 300)
        self.assertEqual(kwargs["params"]["apikey"], "test-key")


    def test_missing_gold_volume_does_not_erase_feature_rows(self):
        index = pd.date_range("2026-01-01", periods=500, freq="5min", tz="UTC")
        close = 2000 + pd.Series(range(500), dtype=float).apply(lambda i: 2 * __import__("math").sin(i / 8))
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

    def test_requires_api_key(self):
        with patch.object(market, "requests", FakeRequests), patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "TWELVEDATA_API_KEY is missing"):
                market._fetch_twelvedata_gold("1h", 500)

    def test_rejects_api_error(self):
        FakeRequests.response = FakeResponse({
            "status": "error",
            "message": "Invalid API key",
        })
        with patch.object(market, "requests", FakeRequests), patch.dict("os.environ", {"TWELVEDATA_API_KEY": "bad-key"}):
            with self.assertRaisesRegex(RuntimeError, "Invalid API key"):
                market._fetch_twelvedata_gold("1h", 500)


if __name__ == "__main__":
    unittest.main()
