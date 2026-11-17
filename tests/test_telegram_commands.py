import unittest
from unittest.mock import Mock, patch
import pandas as pd

from telegram_ar import Telegram


class TelegramCommandTests(unittest.TestCase):
    def setUp(self):
        self.bot = Telegram()
        self.bot.base = 'https://api.telegram.org/botTEST'
        self.bot.sent = []
        self.bot.send = lambda text, chat_id=None, reply_markup=None: self.bot.sent.append((text, chat_id)) or True

    def test_registers_only_supported_slash_commands(self):
        response = Mock(ok=True)
        response.json.return_value = {'ok': True}
        with patch('telegram_ar.requests.post', return_value=response) as post:
            self.assertTrue(self.bot._register_commands())
        payload = post.call_args.kwargs['json']
        self.assertEqual(
            [item['command'] for item in payload['commands']],
            ['menu', 'signal', 'watch', 'price', 'threshold'],
        )

    def test_command_routing_accepts_bot_suffix_and_menu_aliases(self):
        self.bot.send_menu = Mock()
        self.assertTrue(self.bot.handle_command('/start@sample_bot', 123))
        self.assertTrue(self.bot.handle_command('/menu', 123))
        self.assertEqual(self.bot.send_menu.call_count, 2)

    def test_signal_defaults_to_primary_btc_15m_and_accepts_aliases(self):
        self.bot.engine = Mock()
        self.bot.engine.scan.return_value = {'id': 7}
        self.bot.handle_command('/signal', 123)
        self.bot.engine.scan.assert_called_once_with('15m', 'BINANCE:BTCUSDT')
        self.bot.engine.scan.reset_mock()
        self.bot.handle_command('/signal الذهب 1h', 123)
        self.bot.engine.scan.assert_called_once_with('1h', 'TVC:GOLD')

    def test_signal_rejects_invalid_arguments_without_scanning(self):
        self.bot.engine = Mock()
        self.bot.handle_command('/signal ETH 4h', 123)
        self.bot.engine.scan.assert_not_called()
        self.assertIn('صيغة الأمر', self.bot.sent[-1][0])

    def test_watch_reports_automatic_scanner_and_open_trade_counts(self):
        self.bot.engine = Mock()
        self.bot.engine.db.all_open.return_value = [
            {'status': 'WAITING'}, {'status': 'ACTIVE'}, {'status': 'ACTIVE'}
        ]
        self.bot.handle_command('/watch', 123)
        self.assertIn('المراقبة التلقائية تعمل', self.bot.sent[-1][0])
        self.assertIn('بانتظار الدخول: 1', self.bot.sent[-1][0])
        self.assertIn('مفعّلة: 2', self.bot.sent[-1][0])

    def test_price_reports_latest_available_candle_close(self):
        frame = pd.DataFrame(
            {'Close': [65000.5]},
            index=pd.to_datetime(['2026-11-17T09:00:00Z']),
        )
        frame.attrs['market_data_source'] = 'Binance REST'
        with patch('telegram_ar.fetch', return_value=frame) as fetch_market:
            self.bot.handle_command('/price BTC', 123)
        fetch_market.assert_called_once_with('BINANCE:BTCUSDT', '5m', period=300)
        self.assertIn('65000.5000', self.bot.sent[-1][0])
        self.assertIn('Binance REST', self.bot.sent[-1][0])

    def test_threshold_command_uses_asset_specific_live_thresholds(self):
        self.bot.handle_command('/threshold', 123)
        report = self.bot.sent[-1][0]
        self.assertIn('البيتكوين', report)
        self.assertIn('15m: Score ≥ 65 | AI ≥ 30%', report)
        self.assertIn('الذهب', report)

    def test_unknown_command_returns_help_hint(self):
        self.bot.handle_command('/not_a_command', 123)
        self.assertIn('/menu', self.bot.sent[-1][0])


if __name__ == '__main__':
    unittest.main()
