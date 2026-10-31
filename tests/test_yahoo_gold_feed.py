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


class YahooGoldFeedTests(unittest.TestCase):
    def test_fetches_xauusd_spot_and_normalizes_candles(self):
        timestamps = list(range(1_700_000_000, 1_700_000_000 + 300 * 300, 300))
        values = [float(2000 + i) for i in range(len(timestamps))]
        payload = {
            "chart": {
                "error": None,
                "result": [{
                    "timestamp": timestamps,
                    "indicators": {"quote": [{
                        "open": values,
                        "high": [v + 2 for v in values],
                        "low": [v - 2 for v in values],
                        "close": [v + 1 for v in values],
                        "volume": [0] * len(values),
                    }]},
                }],
            }
        }
        with patch.object(market.requests, "get", return_value=FakeResponse(payload)) as get:
            frame = market._fetch_yahoo_gold("5m", 300)

        self.assertEqual(len(frame), 300)
        self.assertEqual(list(frame.columns), ["Open", "High", "Low", "Close", "Volume"])
        self.assertTrue(isinstance(frame.index, pd.DatetimeIndex))
        self.assertEqual(frame.index.tz, pd.Timestamp.now(tz="UTC").tz)
        self.assertIn("XAUUSD", get.call_args.args[0])
        self.assertEqual(get.call_args.kwargs["params"]["interval"], "5m")

    def test_rejects_empty_yahoo_chart_result(self):
        payload = {"chart": {"error": None, "result": []}}
        with patch.object(market.requests, "get", return_value=FakeResponse(payload)):
            with self.assertRaisesRegex(RuntimeError, "no XAU/USD chart result"):
                market._fetch_yahoo_gold("1h", 500)


if __name__ == "__main__":
    unittest.main()
