import unittest
from unittest.mock import patch
import config


class StrategyGateTests(unittest.TestCase):
    def test_5m_both_directions_remain_enabled(self):
        with patch.object(config, "DISABLE_15M_SIGNALS", True), patch.object(config, "DISABLE_1H_SELL_SIGNALS", True):
            self.assertTrue(config.signal_direction_allowed("5m", "BUY"))
            self.assertTrue(config.signal_direction_allowed("5m", "SELL"))

    def test_15m_is_disabled_by_default_policy(self):
        with patch.object(config, "DISABLE_15M_SIGNALS", True):
            self.assertFalse(config.signal_direction_allowed("15m", "BUY"))
            self.assertFalse(config.signal_direction_allowed("15m", "SELL"))

    def test_1h_sell_disabled_but_buy_remains_enabled(self):
        with patch.object(config, "DISABLE_1H_SELL_SIGNALS", True):
            self.assertTrue(config.signal_direction_allowed("1h", "BUY"))
            self.assertFalse(config.signal_direction_allowed("1h", "SELL"))

    def test_gates_can_be_disabled_for_controlled_rollback(self):
        with patch.object(config, "DISABLE_15M_SIGNALS", False), patch.object(config, "DISABLE_1H_SELL_SIGNALS", False):
            self.assertTrue(config.signal_direction_allowed("15m", "BUY"))
            self.assertTrue(config.signal_direction_allowed("1h", "SELL"))


if __name__ == "__main__":
    unittest.main()
