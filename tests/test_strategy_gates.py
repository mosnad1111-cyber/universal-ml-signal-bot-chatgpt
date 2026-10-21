import unittest
from unittest.mock import patch
import config


class StrategyGateTests(unittest.TestCase):
    def test_5m_both_directions_remain_enabled(self):
        with patch.object(config, "DISABLE_15M_SIGNALS", True), patch.object(config, "DISABLE_1H_SELL_SIGNALS", True):
            self.assertTrue(config.signal_direction_allowed("5m", "BUY"))
            self.assertTrue(config.signal_direction_allowed("5m", "SELL"))

    def test_15m_gate_can_still_be_enabled_explicitly(self):
        with patch.object(config, "DISABLE_15M_SIGNALS", True):
            self.assertFalse(config.signal_direction_allowed("15m", "BUY"))
            self.assertFalse(config.signal_direction_allowed("15m", "SELL"))

    def test_1h_sell_gate_can_still_be_enabled_explicitly(self):
        with patch.object(config, "DISABLE_1H_SELL_SIGNALS", True):
            self.assertTrue(config.signal_direction_allowed("1h", "BUY"))
            self.assertFalse(config.signal_direction_allowed("1h", "SELL"))

    def test_15m_and_1h_sell_enabled_by_default(self):
        self.assertFalse(config.DISABLE_15M_SIGNALS)
        self.assertFalse(config.DISABLE_1H_SELL_SIGNALS)
        self.assertTrue(config.signal_direction_allowed("15m", "BUY"))
        self.assertTrue(config.signal_direction_allowed("15m", "SELL"))
        self.assertTrue(config.signal_direction_allowed("1h", "SELL"))

    def test_gates_can_be_disabled_for_controlled_rollback(self):
        with patch.object(config, "DISABLE_15M_SIGNALS", False), patch.object(config, "DISABLE_1H_SELL_SIGNALS", False):
            self.assertTrue(config.signal_direction_allowed("15m", "BUY"))
            self.assertTrue(config.signal_direction_allowed("1h", "SELL"))


if __name__ == "__main__":
    unittest.main()
