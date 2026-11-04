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

    def test_higher_timeframe_context_uses_the_same_exclusive_utc_cutoff(self):
        cutoff = pd.Timestamp("2026-11-04T00:00:00Z")
        context_index = pd.DatetimeIndex([
            "2026-11-03T23:45:00Z",
            "2026-11-04T00:00:00Z",
            "2026-11-04T00:15:00Z",
        ])
        context = pd.DataFrame({"close": [10.0, 11.0, 12.0]}, index=context_index)
        base = pd.DataFrame(
            {"close": [9.0]},
            index=pd.DatetimeIndex(["2026-11-03T23:55:00Z"]),
        )
        seen_indexes = []

        def fake_add_features(frame):
            seen_indexes.append(frame.index.copy())
            return pd.DataFrame({
                "ema20": [2.0] * len(frame),
                "ema50": [1.5] * len(frame),
                "ema200": [1.0] * len(frame),
                "rsi": [55.0] * len(frame),
                "macd_hist": [0.1] * len(frame),
                "dist_ema20": [0.2] * len(frame),
            }, index=frame.index)

        backtest = GoldBacktest()
        with patch("backtest.fetch", side_effect=lambda *args, **kwargs: context.copy()):
            with patch("backtest.add_features", side_effect=fake_add_features):
                enriched = backtest._enrich(
                    "5m", base, symbol="TEST:UTC-CUTOFF", end_before=cutoff
                )

        self.assertEqual(len(seen_indexes), 2)  # 15m and 1h contexts
        for index in seen_indexes:
            self.assertEqual(list(index), [context_index[0]])
            self.assertTrue((index < cutoff).all())
        self.assertIn("ctx15_trend", enriched.columns)
        self.assertIn("ctx1h_trend", enriched.columns)


if __name__ == "__main__":
    unittest.main()
