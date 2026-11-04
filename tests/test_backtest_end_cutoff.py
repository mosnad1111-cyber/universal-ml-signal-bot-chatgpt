import unittest

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


if __name__ == "__main__":
    unittest.main()
