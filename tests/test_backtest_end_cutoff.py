import unittest
from unittest.mock import patch

import pandas as pd

from backtest import GoldBacktest


class BacktestUtcCutoffTests(unittest.TestCase):
    def test_exclusive_cutoff_drops_the_entire_current_utc_day(self):
        index = pd.DatetimeIndex([
            "2026-11-03T23:50:00Z",
            "2026-11-03T23:55:00Z",
            "2026-11-04T00:00:00Z",
            "2026-11-04T00:05:00Z",
            "2026-11-04T12:15:00Z",
        ])
        frame = pd.DataFrame({"close": [1, 2, 3, 4, 5]}, index=index)

        filtered = GoldBacktest._before_exclusive_utc(
            frame, "2026-11-04T00:00:00+00:00"
        )

        self.assertEqual(
            list(filtered.index),
            [index[0], index[1]],
        )
        self.assertTrue((filtered.index < pd.Timestamp("2026-11-04T00:00:00Z")).all())

    def test_naive_market_timestamps_are_interpreted_as_utc(self):
        index = pd.DatetimeIndex([
            "2026-11-03 23:55:00",
            "2026-11-04 00:00:00",
            "2026-11-04 00:05:00",
        ])
        frame = pd.DataFrame({"close": [1, 2, 3]}, index=index)

        filtered = GoldBacktest._before_exclusive_utc(
            frame, "2026-11-04T00:00:00Z"
        )

        self.assertEqual(list(filtered.index), [index[0]])

    def test_cutoff_with_non_utc_offset_is_normalized_to_utc(self):
        index = pd.DatetimeIndex([
            "2026-11-03T23:55:00Z",
            "2026-11-04T00:00:00Z",
            "2026-11-04T00:05:00Z",
        ])
        frame = pd.DataFrame({"close": [1, 2, 3]}, index=index)

        filtered = GoldBacktest._before_exclusive_utc(
            frame, "2026-11-04T02:00:00+02:00"
        )

        self.assertEqual(list(filtered.index), [index[0]])

    def test_prepare_applies_cutoff_before_features_and_keys_cache_by_cutoff(self):
        index = pd.DatetimeIndex([
            "2026-11-03T23:50:00Z",
            "2026-11-03T23:55:00Z",
            "2026-11-04T00:00:00Z",
            "2026-11-04T00:05:00Z",
        ])
        frame = pd.DataFrame({
            "Open": [1.0, 2.0, 3.0, 4.0],
            "High": [1.5, 2.5, 3.5, 4.5],
            "Low": [0.5, 1.5, 2.5, 3.5],
            "Close": [1.2, 2.2, 3.2, 4.2],
            "Volume": [1.0, 1.0, 1.0, 1.0],
        }, index=index)
        frame.attrs["market_data_source"] = "unit-test historical OHLC"

        backtest = GoldBacktest()
        backtest._enrich = lambda timeframe, features, symbol, end_before=None: features.copy()
        with patch("backtest.fetch", side_effect=lambda *args, **kwargs: frame.copy()) as fetch_mock:
            with patch("backtest.add_features", side_effect=lambda data: data):
                first = backtest._prepare(
                    "5m", 10, symbol="TEST:UTC-CUTOFF",
                    end_before="2026-11-04T00:00:00Z",
                )
                second = backtest._prepare(
                    "5m", 10, symbol="TEST:UTC-CUTOFF",
                    end_before="2026-11-04T00:05:00Z",
                )

        self.assertEqual(len(first["raw"]), 2)
        self.assertEqual(first["received_bars"], 4)
        self.assertEqual(first["end_window_excluded_bars"], 2)
        self.assertEqual(first["snapshot_end"], index[1].isoformat())
        self.assertEqual(len(second["raw"]), 3)
        self.assertEqual(second["end_window_excluded_bars"], 1)
        self.assertEqual(fetch_mock.call_count, 2)


if __name__ == "__main__":
    unittest.main()
