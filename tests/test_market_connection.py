import os
import unittest
from unittest.mock import patch

import market


class BrokenTVClient:
    def __init__(self):
        self.calls = 0

    def get_hist(self, **kwargs):
        self.calls += 1
        raise RuntimeError("WebSocket receive error: Connection to remote host was lost")


class MarketConnectionTests(unittest.TestCase):
    def setUp(self):
        self.old_tv = market._tv
        market._tv = None
        with market._cache_lock:
            market._cache.clear()

    def tearDown(self):
        market._tv = self.old_tv
        with market._cache_lock:
            market._cache.clear()

    def test_socket_failure_does_not_retry_every_smaller_bar_window(self):
        client = BrokenTVClient()
        fake_interval = type("FakeInterval", (), {"in_5_minute": "5m"})
        with patch.object(market, "_fetch_binance_rest", side_effect=RuntimeError("Binance unavailable")), patch.object(market, "_get_tv", return_value=client), patch.object(
            market, "Interval", fake_interval
        ), patch.object(market.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "بعد إعادة المحاولة"):
                market.fetch("BINANCE:BTCUSDT", "5m", period=5000)

        self.assertEqual(client.calls, 3)

    def test_tradingview_credentials_are_used_when_configured(self):
        market._tv = None
        with patch.dict(os.environ, {"TV_USERNAME": "tv-user", "TV_PASSWORD": "tv-password"}), patch.object(
            market, "TvDatafeed"
        ) as tv_class:
            client = market._get_tv()

        tv_class.assert_called_once_with(username="tv-user", password="tv-password")
        self.assertIs(client, tv_class.return_value)


if __name__ == "__main__":
    unittest.main()
