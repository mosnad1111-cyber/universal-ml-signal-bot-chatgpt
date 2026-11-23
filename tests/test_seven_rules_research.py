import unittest
from datetime import time

import numpy as np
import pandas as pd

from research.seven_rules_research import (
    _a_plus,
    _metrics,
    _simulate_trade,
    generate_candidates,
    prepare_bars,
)


def _intraday_frame(start="2026-06-01 04:00", end="2026-06-01 10:00"):
    index = pd.date_range(start, end, freq="5min", tz="America/New_York").tz_convert("UTC")
    close = np.full(len(index), 100.0)
    frame = pd.DataFrame({
        "Open": close.copy(),
        "High": close + 0.5,
        "Low": close - 0.5,
        "Close": close.copy(),
        "atr14": np.ones(len(index)),
        "ny_date": [ts.tz_convert("America/New_York").date().isoformat() for ts in index],
        "ny_clock": [ts.tz_convert("America/New_York").time().replace(tzinfo=None) for ts in index],
        "daily_bias": ["UP"] * len(index),
        "market_structure": ["UP"] * len(index),
        "adx14": np.full(len(index), 25.0),
        "body_fraction": np.full(len(index), 0.6),
        "bullish": np.zeros(len(index), dtype=bool),
        "bearish": np.zeros(len(index), dtype=bool),
    }, index=index)
    return frame


class SevenRulesResearchTests(unittest.TestCase):
    def test_daily_bias_uses_only_previous_completed_new_york_day(self):
        index = pd.date_range("2026-01-01", periods=55 * 24 * 12, freq="5min", tz="UTC")
        close = np.full(len(index), 100.0)
        # Strong rise only during the final observed New York day.
        last_date = index[-1].tz_convert("America/New_York").date()
        local_dates = [ts.tz_convert("America/New_York").date() for ts in index]
        last_positions = [i for i, d in enumerate(local_dates) if d == last_date]
        close[last_positions] = np.linspace(100.0, 180.0, len(last_positions))
        frame = pd.DataFrame({
            "Open": close, "High": close + 0.2, "Low": close - 0.2, "Close": close,
        }, index=index)
        prepared = prepare_bars(frame)
        current_day = prepared[prepared["ny_date"] == last_date.isoformat()]
        self.assertTrue((current_day["daily_bias"] == "RANGE").all())

    def test_opening_range_uses_only_the_first_0930_five_minute_bar(self):
        frame = _intraday_frame()
        at = {clock: i for i, clock in enumerate(frame["ny_clock"])}
        opening_i = at[time(9, 30)]
        breakout_i = at[time(9, 35)]
        retest_i = at[time(9, 40)]
        entry_i = at[time(9, 45)]
        frame.iloc[opening_i, frame.columns.get_loc("High")] = 101.0
        frame.iloc[opening_i, frame.columns.get_loc("Low")] = 99.0
        frame.iloc[breakout_i, frame.columns.get_loc("Open")] = 100.5
        frame.iloc[breakout_i, frame.columns.get_loc("Close")] = 102.0
        frame.iloc[breakout_i, frame.columns.get_loc("High")] = 102.2
        frame.iloc[breakout_i, frame.columns.get_loc("Low")] = 100.4
        frame.iloc[retest_i, frame.columns.get_loc("Open")] = 100.8
        frame.iloc[retest_i, frame.columns.get_loc("Close")] = 101.2
        frame.iloc[retest_i, frame.columns.get_loc("High")] = 101.4
        frame.iloc[retest_i, frame.columns.get_loc("Low")] = 100.7
        frame.iloc[retest_i, frame.columns.get_loc("bullish")] = True
        frame.iloc[entry_i, frame.columns.get_loc("Open")] = 101.5

        candidates = generate_candidates(frame, "opening_range_breakout_retest")
        buys = [c for c in candidates if c["side"] == "BUY"]
        self.assertEqual(len(buys), 1)
        self.assertAlmostEqual(buys[0]["level"], 101.0)
        self.assertEqual(buys[0]["entry_i"], entry_i)
        self.assertAlmostEqual(buys[0]["entry"], 101.5)

    def test_single_candle_strategy_uses_last_opposing_candle_as_zone(self):
        frame = _intraday_frame()
        at = {clock: i for i, clock in enumerate(frame["ny_clock"])}
        zone_i, retest_i, entry_i = at[time(9, 35)], at[time(9, 40)], at[time(9, 45)]
        frame.iloc[zone_i, frame.columns.get_loc("Open")] = 100.5
        frame.iloc[zone_i, frame.columns.get_loc("Close")] = 99.5
        frame.iloc[zone_i, frame.columns.get_loc("High")] = 101.0
        frame.iloc[zone_i, frame.columns.get_loc("Low")] = 99.0
        frame.iloc[zone_i, frame.columns.get_loc("bearish")] = True
        frame.iloc[retest_i, frame.columns.get_loc("Open")] = 100.0
        frame.iloc[retest_i, frame.columns.get_loc("Close")] = 101.2
        frame.iloc[retest_i, frame.columns.get_loc("High")] = 101.4
        frame.iloc[retest_i, frame.columns.get_loc("Low")] = 99.4
        frame.iloc[retest_i, frame.columns.get_loc("bullish")] = True
        frame.iloc[entry_i, frame.columns.get_loc("Open")] = 101.3

        candidates = generate_candidates(frame, "single_candle_support_resistance")
        self.assertTrue(any(c["side"] == "BUY" for c in candidates))
        buy = next(c for c in candidates if c["side"] == "BUY")
        self.assertEqual(buy["signal_i"], retest_i)
        self.assertEqual(buy["entry_i"], entry_i)
        self.assertLess(buy["stop"], 99.0)

    def test_a_plus_requires_daily_and_intraday_direction_agreement(self):
        frame = _intraday_frame()
        candidate = {"signal_i": 0, "side": "BUY"}
        self.assertTrue(_a_plus(candidate, frame))
        frame.iloc[0, frame.columns.get_loc("daily_bias")] = "RANGE"
        self.assertFalse(_a_plus(candidate, frame))

    def test_same_bar_stop_and_target_is_counted_as_stop(self):
        index = pd.date_range(
            "2026-06-01 09:35", periods=3, freq="5min", tz="America/New_York"
        ).tz_convert("UTC")
        frame = pd.DataFrame({
            "Open": [100.0, 100.0, 100.0],
            "High": [102.5, 101.0, 101.0],
            "Low": [98.5, 99.5, 99.5],
            "Close": [100.0, 100.5, 100.5],
            "ny_date": ["2026-06-01"] * 3,
            "ny_clock": [time(9, 35), time(9, 40), time(9, 45)],
        }, index=index)
        candidate = {
            "strategy": "fixture", "setup": "fixture", "side": "BUY",
            "signal_i": 0, "entry_i": 0, "entry": 100.0,
            "stop": 99.0, "target": 102.0, "risk": 1.0,
        }
        trade = _simulate_trade(frame, candidate)
        self.assertEqual(trade["result"], "SL_SAME_BAR_AMBIGUOUS")
        self.assertAlmostEqual(trade["gross_r"], -1.0)

    def test_trade_metrics_are_reported_separately_for_buy_and_sell(self):
        trades = [
            {"gross_r": 2.0, "side": "BUY", "minutes_held": 10, "bars_to_05r": 1},
            {"gross_r": -1.0, "side": "SELL", "minutes_held": 15, "bars_to_05r": None},
        ]
        metrics = _metrics(trades)
        self.assertEqual(metrics["trades"], 2)
        self.assertEqual(metrics["wins"], 1)
        self.assertAlmostEqual(metrics["net_r_cost_005"], 0.9)
        self.assertAlmostEqual(metrics["expectancy_r_cost_005"], 0.45)


if __name__ == "__main__":
    unittest.main()
