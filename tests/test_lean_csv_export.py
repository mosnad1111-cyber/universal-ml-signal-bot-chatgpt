import csv
import tempfile
import unittest
from pathlib import Path

from scripts.prepare_lean_backtest import _write_timeframe


class LeanCsvExportTests(unittest.TestCase):
    def test_snapshot_is_written_as_one_ordered_stream_across_utc_midnight(self):
        bars = []
        for stamp, price in (
            ("2026-11-02T23:50:00+00:00", 100.0),
            ("2026-11-02T23:55:00+00:00", 101.0),
            ("2026-11-03T00:00:00+00:00", 102.0),
        ):
            bars.append({
                "time": stamp,
                "open": price,
                "high": price + 1,
                "low": price - 1,
                "close": price + 0.5,
                "volume": 0.0,
            })

        result = {
            "market_bars": bars,
            "lean_events": [],
            "symbol": "XAUUSD",
            "asset_label": "Gold",
            "market_data_source": "Dukascopy XAU/USD BID historical OHLC",
            "snapshot_start": bars[0]["time"],
            "snapshot_end": bars[-1]["time"],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            metadata = _write_timeframe(root, "GOLD", "5m", result, "a" * 32)
            csv_path = root / "5m" / "bars.csv"
            self.assertTrue(csv_path.is_file())
            self.assertFalse((root / "5m" / "bars").exists())
            with csv_path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))

        self.assertEqual(len(rows), metadata["bars_count"])
        self.assertEqual(len(rows), 3)
        self.assertEqual(
            [row["time"] for row in rows],
            [
                "2026-11-02 23:50:00.000000",
                "2026-11-02 23:55:00.000000",
                "2026-11-03 00:00:00.000000",
            ],
        )
        self.assertEqual(
            metadata["daily_bar_counts"],
            {"20261102": 2, "20261103": 1},
        )

    def test_export_rejects_signal_timestamp_without_matching_ohlc_candle(self):
        bars = [{
            "time": "2026-11-03T23:55:00+00:00",
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 0.0,
        }]
        result = {
            "market_bars": bars,
            "lean_events": [{"signal_time": "2026-11-03T23:50:00+00:00"}],
            "symbol": "XAUUSD",
            "asset_label": "Gold",
            "market_data_source": "Dukascopy XAU/USD BID historical OHLC",
            "snapshot_start": bars[0]["time"],
            "snapshot_end": bars[0]["time"],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(RuntimeError, "signal timestamps do not match"):
                _write_timeframe(Path(temp_dir), "GOLD", "5m", result, "b" * 32)


if __name__ == "__main__":
    unittest.main()
