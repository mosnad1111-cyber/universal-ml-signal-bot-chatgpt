import os
import tempfile
import unittest

from db import DB


class ActivationCandlePersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DB(os.path.join(self.tmp.name, 'signals.sqlite3'))
        self.db.set_setting('signal_delivery_fix_v1', '1')
        self.signal = {
            'symbol': 'TVC:GOLD', 'timeframe': '5m', 'direction': 'BUY',
            'entry': 2000.0, 'sl': 1990.0, 'tp': 2020.0,
            'score': 80.0, 'ai_prob': 0.7, 'wave_key': 'wave-1',
            'candle_time': '2026-11-02T10:00:00+00:00', 'features': {},
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_activation_candle_is_persisted(self):
        signal_id = self.db.create(self.signal)
        self.db.activate(signal_id, self.signal['entry'], '2026-11-02T10:05:00+00:00')
        row = self.db.all_open()[0]
        self.assertEqual(row['status'], 'ACTIVE')
        self.assertEqual(row['activation_candle_time'], '2026-11-02T10:05:00+00:00')

    def test_old_database_is_migrated(self):
        self.db.create(self.signal)
        with self.db._conn() as conn:
            columns = {row[1] for row in conn.execute('PRAGMA table_info(signals)')}
        self.assertIn('activation_candle_time', columns)


if __name__ == '__main__':
    unittest.main()
