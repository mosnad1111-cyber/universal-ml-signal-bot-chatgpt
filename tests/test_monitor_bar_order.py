import unittest

from engine import Engine


class MonitorBarOrderTests(unittest.TestCase):
    def test_same_bar_stop_and_target_is_stop(self):
        self.assertEqual(Engine._bar_exit('BUY', 120, 80, 90, 110), 'SL')

    def test_later_bar_target_is_target(self):
        self.assertEqual(Engine._bar_exit('BUY', 111, 95, 90, 110), 'TP')

    def test_activation_bar_target_touch_is_ambiguous_loss(self):
        self.assertEqual(Engine._activation_bar_exit('BUY', 112, 95, 90, 110), 'SL')

    def test_activation_bar_without_exit_touch_stays_open(self):
        self.assertIsNone(Engine._activation_bar_exit('BUY', 108, 95, 90, 110))


if __name__ == '__main__':
    unittest.main()
