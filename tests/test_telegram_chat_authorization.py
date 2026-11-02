import unittest

from telegram_ar import Telegram


class FakeDB:
    def __init__(self, saved=None):
        self.saved = saved

    def get_setting(self, key):
        return self.saved

    def set_setting(self, key, value):
        self.saved = str(value)


class TelegramChatAuthorizationTests(unittest.TestCase):
    def test_registered_chat_cannot_be_replaced(self):
        db = FakeDB('12345')
        bot = Telegram(db)
        self.assertTrue(bot._authorized_chat(12345))
        self.assertFalse(bot._authorized_chat(67890))
        self.assertFalse(bot._remember_chat(67890))
        self.assertEqual(db.saved, '12345')

    def test_first_chat_can_pair_once(self):
        db = FakeDB()
        bot = Telegram(db)
        self.assertTrue(bot._authorized_chat(12345))
        self.assertTrue(bot._remember_chat(12345))
        self.assertFalse(bot._authorized_chat(67890))
        self.assertEqual(db.saved, '12345')


if __name__ == '__main__':
    unittest.main()
