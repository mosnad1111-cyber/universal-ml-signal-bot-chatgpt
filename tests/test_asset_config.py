import unittest
from config import ASSETS, GOLD_DATA_SYMBOL, BTC_DATA_SYMBOL


class AssetConfigTests(unittest.TestCase):
    def test_gold_feed_is_unchanged(self):
        self.assertEqual(GOLD_DATA_SYMBOL, "TVC:GOLD")
        self.assertEqual(ASSETS["GOLD"]["exchange"], "TVC")
        self.assertEqual(ASSETS["GOLD"]["ticker"], "GOLD")

    def test_bitcoin_uses_binance_btcusdt(self):
        self.assertEqual(BTC_DATA_SYMBOL, "BINANCE:BTCUSDT")
        self.assertEqual(ASSETS["BTC"]["exchange"], "BINANCE")
        self.assertEqual(ASSETS["BTC"]["ticker"], "BTCUSDT")

    def test_symbols_are_distinct_and_have_arabic_labels(self):
        self.assertNotEqual(ASSETS["GOLD"]["symbol"], ASSETS["BTC"]["symbol"])
        self.assertTrue(ASSETS["GOLD"]["label"])
        self.assertTrue(ASSETS["BTC"]["label"])


if __name__ == "__main__":
    unittest.main()
