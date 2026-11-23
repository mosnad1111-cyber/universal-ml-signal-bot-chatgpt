"""Research-only test of three intraday playbooks on XAUUSD and BTCUSDT.

This module never changes production signal generation or order execution.
The research execution timeframe is 5 minutes because that is the common
historical feed currently supported by market.fetch().
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

NY = "America/New_York"
BAR_MINUTES = 5
PREMARKET_START = time(4, 0)
SESSION_START = time(9, 30)
ENTRY_START = time(9, 35)
ENTRY_CUTOFF = time(15, 45)
FORCE_FLAT = time(15, 55)
PREMARKET_MIN_BARS = 50
MAX_RETEST_BARS = 6
MAX_SINGLE_CANDLE_AGE = 24
ATR_PERIOD = 14
DAILY_FAST_EMA = 20
DAILY_SLOW_EMA = 50
INTRADAY_FAST_EMA = 20
INTRADAY_SLOW_EMA = 50
ADX_MIN = 18.0
A_PLUS_BODY_FRACTION = 0.40
STOP_ATR_BUFFER = 0.10
TARGET_R = 2.0
QUICK_FAILURE_BARS = 3
QUICK_FAILURE_MFE_R = 0.50
COST_SENSITIVITY_R = (0.0, 0.05, 0.10)

STRATEGIES = (
    "premarket_breakout_retest",
    "single_candle_support_resistance",
    "opening_range_breakout_retest",
)
STRATEGY_LABELS = {
    "premarket_breakout_retest": "Premarket high/low breakout + retest",
    "single_candle_support_resistance": "Single opposing candle support/resistance",
    "opening_range_breakout_retest": "First 5-minute opening range breakout + retest",
}
VARIANTS = ("base", "a_plus", "a_plus_fast_exit")


def _wilder(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def prepare_bars(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize OHLC and add strictly lagged daily bias and intraday structure."""
    if frame is None or frame.empty:
        raise ValueError("No OHLC bars supplied")
    x = frame.copy()
    x.index = pd.to_datetime(x.index, utc=True, errors="coerce")
    x = x.loc[~x.index.isna()]
    x = x[~x.index.duplicated(keep="last")].sort_index()
    required = ("Open", "High", "Low", "Close")
    missing = [name for name in required if name not in x.columns]
    if missing:
        lower = {str(c).lower(): c for c in x.columns}
        if all(name.lower() in lower for name in required):
            x = x.rename(columns={lower[name.lower()]: name for name in required})
        else:
            raise ValueError("Missing OHLC columns: " + ", ".join(missing))
    for name in required:
        x[name] = pd.to_numeric(x[name], errors="coerce")
    x = x.dropna(subset=list(required))
    x = x[(x["High"] >= x[["Open", "Close", "Low"]].max(axis=1)) &
          (x["Low"] <= x[["Open", "Close", "High"]].min(axis=1))]
    if len(x) < 100:
        raise ValueError(f"Only {len(x)} valid candles; at least 100 are required")
    if "Volume" not in x:
        x["Volume"] = np.nan

    local = x.index.tz_convert(NY)
    x["ny_date"] = [ts.date().isoformat() for ts in local]
    x["ny_clock"] = [ts.time().replace(tzinfo=None) for ts in local]

    previous_close = x["Close"].shift(1)
    true_range = pd.concat(
        [
            x["High"] - x["Low"],
            (x["High"] - previous_close).abs(),
            (x["Low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    x["atr14"] = _wilder(true_range, ATR_PERIOD)
    x["ema20"] = x["Close"].ewm(span=INTRADAY_FAST_EMA, adjust=False).mean()
    x["ema50"] = x["Close"].ewm(span=INTRADAY_SLOW_EMA, adjust=False).mean()

    up_move = x["High"].diff()
    down_move = -x["Low"].diff()
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
        index=x.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=x.index,
    )
    atr_safe = x["atr14"].replace(0, np.nan)
    plus_di = 100.0 * _wilder(plus_dm, ATR_PERIOD) / atr_safe
    minus_di = 100.0 * _wilder(minus_dm, ATR_PERIOD) / atr_safe
    denominator = (plus_di + minus_di).replace(0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / denominator
    x["adx14"] = _wilder(dx, ATR_PERIOD)

    # Aggregate by New York calendar date, then shift by one full observed day.
    # A day's bias is therefore never calculated with that same day's future close.
    daily = x.groupby("ny_date", sort=True).agg(
        daily_close=("Close", "last"),
    )
    daily["ema20"] = daily["daily_close"].ewm(
        span=DAILY_FAST_EMA, adjust=False, min_periods=DAILY_FAST_EMA
    ).mean()
    daily["ema50"] = daily["daily_close"].ewm(
        span=DAILY_SLOW_EMA, adjust=False, min_periods=DAILY_SLOW_EMA
    ).mean()
    daily["bias"] = "RANGE"
    up = (daily["daily_close"] > daily["ema20"]) & (daily["ema20"] > daily["ema50"])
    down = (daily["daily_close"] < daily["ema20"]) & (daily["ema20"] < daily["ema50"])
    daily.loc[up, "bias"] = "UP"
    daily.loc[down, "bias"] = "DOWN"
    prior_bias = daily["bias"].shift(1).fillna("RANGE")
    x["daily_bias"] = x["ny_date"].map(prior_bias.to_dict()).fillna("RANGE")

    x["body_fraction"] = (
        (x["Close"] - x["Open"]).abs()
        / (x["High"] - x["Low"]).replace(0, np.nan)
    ).fillna(0.0).clip(0.0, 1.0)
    x["bullish"] = x["Close"] > x["Open"]
    x["bearish"] = x["Close"] < x["Open"]
    x["market_structure"] = "RANGE"
    strong_up = (
        (x["ema20"] > x["ema50"]) & (x["Close"] > x["ema20"])
        & (x["adx14"] >= ADX_MIN)
    )
    strong_down = (
        (x["ema20"] < x["ema50"]) & (x["Close"] < x["ema20"])
        & (x["adx14"] >= ADX_MIN)
    )
    x.loc[strong_up, "market_structure"] = "UP"
    x.loc[strong_down, "market_structure"] = "DOWN"
    return x


def _is_session(clock: time) -> bool:
    return SESSION_START <= clock <= ENTRY_CUTOFF


def _is_entry_time(clock: time) -> bool:
    return ENTRY_START <= clock <= ENTRY_CUTOFF


def _a_plus(candidate: dict[str, Any], frame: pd.DataFrame) -> bool:
    row = frame.iloc[int(candidate["signal_i"])]
    expected = "UP" if candidate["side"] == "BUY" else "DOWN"
    return bool(
        row["daily_bias"] == expected
        and row["market_structure"] == expected
        and float(row["body_fraction"]) >= A_PLUS_BODY_FRACTION
        and np.isfinite(row["adx14"])
        and float(row["adx14"]) >= ADX_MIN
    )


def _candidate(
    frame: pd.DataFrame,
    signal_i: int,
    side: str,
    level: float,
    stop: float,
    strategy: str,
    setup: str,
) -> dict[str, Any] | None:
    entry_i = signal_i + 1
    if entry_i >= len(frame):
        return None
    signal_row = frame.iloc[signal_i]
    entry_row = frame.iloc[entry_i]
    if signal_row["ny_date"] != entry_row["ny_date"]:
        return None
    if not _is_entry_time(entry_row["ny_clock"]):
        return None
    entry = float(entry_row["Open"])
    risk = entry - stop if side == "BUY" else stop - entry
    if not np.isfinite(risk) or risk <= 0:
        return None
    target = entry + TARGET_R * risk if side == "BUY" else entry - TARGET_R * risk
    return {
        "strategy": strategy,
        "setup": setup,
        "side": side,
        "level": float(level),
        "signal_i": int(signal_i),
        "entry_i": int(entry_i),
        "entry": entry,
        "stop": float(stop),
        "target": float(target),
        "risk": float(risk),
    }


def _break_retest_candidates(
    day: pd.DataFrame,
    strategy: str,
    high_level: float,
    low_level: float,
    start_clock: time,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    indices = list(range(len(day)))
    for side, level in (("BUY", high_level), ("SELL", low_level)):
        found = False
        for i in indices:
            row = day.iloc[i]
            if row["ny_clock"] < start_clock or row["ny_clock"] > ENTRY_CUTOFF:
                continue
            if i == 0:
                continue
            prev = day.iloc[i - 1]
            if side == "BUY":
                broke = float(prev["Close"]) <= level and float(row["Close"]) > level
            else:
                broke = float(prev["Close"]) >= level and float(row["Close"]) < level
            if not broke:
                continue
            for j in range(i + 1, min(i + MAX_RETEST_BARS + 1, len(day) - 1)):
                retest = day.iloc[j]
                if retest["ny_clock"] > ENTRY_CUTOFF:
                    break
                atr = float(retest["atr14"]) if np.isfinite(retest["atr14"]) else np.nan
                if not np.isfinite(atr) or atr <= 0:
                    continue
                tolerance = 0.10 * atr
                if side == "BUY":
                    confirmed = (
                        float(retest["Low"]) <= level + tolerance
                        and float(retest["Close"]) >= level
                        and bool(retest["bullish"])
                    )
                    stop = min(level, float(retest["Low"])) - STOP_ATR_BUFFER * atr
                else:
                    confirmed = (
                        float(retest["High"]) >= level - tolerance
                        and float(retest["Close"]) <= level
                        and bool(retest["bearish"])
                    )
                    stop = max(level, float(retest["High"])) + STOP_ATR_BUFFER * atr
                if not confirmed:
                    continue
                candidate = _candidate(
                    day, j, side, level, stop, strategy,
                    "breakout_retest_" + side.lower(),
                )
                if candidate:
                    output.append(candidate)
                found = True
                break
            if found:
                break
    return output


def generate_candidates(frame: pd.DataFrame, strategy: str) -> list[dict[str, Any]]:
    """Generate confirmed 5m setups; entries are next-bar opens to avoid look-ahead."""
    if strategy not in STRATEGIES:
        raise ValueError(f"Unsupported strategy: {strategy}")
    output: list[dict[str, Any]] = []
    for _, day in frame.groupby("ny_date", sort=True):
        day = day.sort_index().reset_index(drop=False)
        # reset_index keeps timestamp in a column; calculations use positional rows.
        clocks = day["ny_clock"]
        if strategy == "premarket_breakout_retest":
            mask = clocks.map(lambda t: PREMARKET_START <= t < SESSION_START)
            premarket = day.loc[mask]
            if len(premarket) < PREMARKET_MIN_BARS:
                continue
            high_level = float(premarket["High"].max())
            low_level = float(premarket["Low"].min())
            output.extend(_break_retest_candidates(
                day, strategy, high_level, low_level, ENTRY_START
            ))
        elif strategy == "opening_range_breakout_retest":
            opening = day.loc[clocks == SESSION_START]
            if opening.empty:
                continue
            opening_row = opening.iloc[0]
            high_level = float(opening_row["High"])
            low_level = float(opening_row["Low"])
            # Only scan candles after the first 5-minute opening candle.
            output.extend(_break_retest_candidates(
                day, strategy, high_level, low_level, ENTRY_START
            ))
        else:
            active_zone: dict[str, Any] | None = None
            for i in range(len(day) - 1):
                row = day.iloc[i]
                if not _is_session(row["ny_clock"]):
                    continue
                bias = row["daily_bias"]
                if bias == "UP" and bool(row["bearish"]):
                    active_zone = {
                        "side": "BUY", "low": float(row["Low"]),
                        "high": float(row["High"]), "created_i": i,
                    }
                    continue
                if bias == "DOWN" and bool(row["bullish"]):
                    active_zone = {
                        "side": "SELL", "low": float(row["Low"]),
                        "high": float(row["High"]), "created_i": i,
                    }
                    continue
                if not active_zone or i <= active_zone["created_i"]:
                    continue
                if i - active_zone["created_i"] > MAX_SINGLE_CANDLE_AGE:
                    active_zone = None
                    continue
                atr = float(row["atr14"]) if np.isfinite(row["atr14"]) else np.nan
                if not np.isfinite(atr) or atr <= 0:
                    continue
                if active_zone["side"] == "BUY":
                    confirmed = (
                        float(row["Low"]) <= active_zone["high"]
                        and float(row["Close"]) > active_zone["high"]
                        and bool(row["bullish"])
                    )
                    stop = active_zone["low"] - STOP_ATR_BUFFER * atr
                else:
                    confirmed = (
                        float(row["High"]) >= active_zone["low"]
                        and float(row["Close"]) < active_zone["low"]
                        and bool(row["bearish"])
                    )
                    stop = active_zone["high"] + STOP_ATR_BUFFER * atr
                if confirmed:
                    candidate = _candidate(
                        day, i, active_zone["side"], active_zone["high"]
                        if active_zone["side"] == "BUY" else active_zone["low"],
                        stop, strategy, "single_candle_retest_" + active_zone["side"].lower(),
                    )
                    if candidate:
                        output.append(candidate)
                    active_zone = None

    # Convert positional indices from the daily slice back to the full frame.
    # Each day slice is reset_index and starts at local position zero, so retain
    # UTC timestamps and resolve them against the original frame instead.
    positions = {ts: i for i, ts in enumerate(frame.index)}
    remapped = []
    for item in output:
        # The generated record uses local integer positions; the stored timestamp
        # column is not inside the candidate, so map using a private timestamp key.
        remapped.append(item)
    return remapped


def _generate_candidates_global(frame: pd.DataFrame, strategy: str) -> list[dict[str, Any]]:
    """Run each daily slice then remap local positions to global candle positions."""
    output = []
    for _, day in frame.groupby("ny_date", sort=True):
        day = day.copy()
        # generate_candidates expects a whole frame and groups by NY date.
        local_candidates = generate_candidates(day, strategy)
        local_positions = {ts: i for i, ts in enumerate(day.index)}
        for item in local_candidates:
            entry_local = int(item["entry_i"])
            signal_local = int(item["signal_i"])
            item = dict(item)
            item["entry_i"] = int(frame.index.get_indexer([day.index[entry_local]])[0])
            item["signal_i"] = int(frame.index.get_indexer([day.index[signal_local]])[0])
            output.append(item)
    # Stable de-duplication by strategy, side and entry candle.
    dedup = {}
    for item in output:
        dedup[(item["strategy"], item["side"], item["entry_i"])] = item
    return sorted(dedup.values(), key=lambda item: item["entry_i"])


def _simulate_trade(
    frame: pd.DataFrame,
    candidate: dict[str, Any],
    quick_failure: bool = False,
) -> dict[str, Any] | None:
    entry_i = int(candidate["entry_i"])
    entry_row = frame.iloc[entry_i]
    trade_date = entry_row["ny_date"]
    side = candidate["side"]
    entry = float(candidate["entry"])
    stop = float(candidate["stop"])
    target = float(candidate["target"])
    risk = float(candidate["risk"])
    last_session_i = None
    exit_i = exit_price = result = None

    for i in range(entry_i, len(frame)):
        row = frame.iloc[i]
        if row["ny_date"] != trade_date:
            break
        if row["ny_clock"] <= FORCE_FLAT:
            last_session_i = i
        else:
            break

        stop_hit = float(row["Low"]) <= stop if side == "BUY" else float(row["High"]) >= stop
        target_hit = float(row["High"]) >= target if side == "BUY" else float(row["Low"]) <= target
        if stop_hit:
            exit_i, exit_price, result = i, stop, "SL" if not target_hit else "SL_SAME_BAR_AMBIGUOUS"
            break
        if target_hit:
            exit_i, exit_price, result = i, target, "TP"
            break

        if quick_failure and i - entry_i + 1 >= QUICK_FAILURE_BARS:
            if side == "BUY":
                mfe = (float(frame.iloc[entry_i:i + 1]["High"].max()) - entry) / risk
            else:
                mfe = (entry - float(frame.iloc[entry_i:i + 1]["Low"].min())) / risk
            if mfe < QUICK_FAILURE_MFE_R:
                exit_i, exit_price, result = i, float(row["Close"]), "QUICK_FAILURE_EXIT"
                break

        if row["ny_clock"] >= FORCE_FLAT:
            exit_i, exit_price, result = i, float(row["Close"]), "SESSION_CLOSE"
            break
    else:
        exit_i = exit_price = result = None

    if exit_i is None:
        if last_session_i is None or frame.iloc[last_session_i]["ny_clock"] < time(15, 45):
            # Do not score an unfinished current session as a completed trade.
            return None
        exit_i = last_session_i
        exit_price = float(frame.iloc[exit_i]["Close"])
        result = "SESSION_CLOSE"

    gross_r = (
        (float(exit_price) - entry) / risk
        if side == "BUY" else (entry - float(exit_price)) / risk
    )
    segment = frame.iloc[entry_i:exit_i + 1]
    if side == "BUY":
        reached_half_r = np.flatnonzero(segment["High"].to_numpy(dtype=float) >= entry + 0.5 * risk)
    else:
        reached_half_r = np.flatnonzero(segment["Low"].to_numpy(dtype=float) <= entry - 0.5 * risk)
    bars_to_half_r = int(reached_half_r[0] + 1) if len(reached_half_r) else None
    return {
        "strategy": candidate["strategy"],
        "setup": candidate["setup"],
        "side": side,
        "signal_time_utc": frame.index[int(candidate["signal_i"])].isoformat(),
        "entry_time_utc": frame.index[entry_i].isoformat(),
        "exit_time_utc": frame.index[exit_i].isoformat(),
        "entry": round(entry, 8),
        "stop": round(stop, 8),
        "target": round(target, 8),
        "exit_price": round(float(exit_price), 8),
        "risk_points": round(risk, 8),
        "result": result,
        "gross_r": round(float(gross_r), 5),
        "bars_held": int(exit_i - entry_i + 1),
        "minutes_held": int((exit_i - entry_i + 1) * BAR_MINUTES),
        "bars_to_05r": bars_to_half_r,
        "entry_ny": frame.index[entry_i].tz_convert(NY).isoformat(),
        "exit_ny": frame.index[exit_i].tz_convert(NY).isoformat(),
    }


def _metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {
            "trades": 0, "wins": 0, "losses": 0, "breakeven": 0,
            "win_rate_pct": None, "net_win_rate_cost_005_pct": None, "gross_r": 0.0,
            "net_r_cost_000": 0.0, "net_r_cost_005": 0.0, "net_r_cost_010": 0.0,
            "expectancy_r_cost_005": None, "profit_factor_cost_005": None,
            "max_drawdown_r_cost_005": 0.0, "avg_minutes_held": None,
            "median_bars_to_05r": None,
        }
    gross = np.array([float(t["gross_r"]) for t in trades], dtype=float)
    n = len(gross)
    def profit_factor(values: np.ndarray) -> float | None:
        gains = float(values[values > 0].sum())
        losses = abs(float(values[values < 0].sum()))
        return round(gains / losses, 4) if losses > 0 else ("∞" if gains > 0 else None)
    def max_drawdown(values: np.ndarray) -> float:
        equity = np.cumsum(values)
        peaks = np.maximum.accumulate(np.maximum(equity, 0.0))
        return round(float(np.max(peaks - equity)), 4) if len(equity) else 0.0

    net_000, net_005, net_010 = gross, gross - 0.05, gross - 0.10
    half_bars = [int(t["bars_to_05r"]) for t in trades if t.get("bars_to_05r") is not None]
    return {
        "trades": n,
        "wins": int((gross > 0).sum()),
        "losses": int((gross < 0).sum()),
        "breakeven": int((gross == 0).sum()),
        "win_rate_pct": round(100.0 * float((gross > 0).sum()) / n, 2),
        "net_win_rate_cost_005_pct": round(100.0 * float((net_005 > 0).sum()) / n, 2),
        "gross_r": round(float(gross.sum()), 4),
        "net_r_cost_000": round(float(net_000.sum()), 4),
        "net_r_cost_005": round(float(net_005.sum()), 4),
        "net_r_cost_010": round(float(net_010.sum()), 4),
        "expectancy_r_cost_005": round(float(net_005.mean()), 5),
        "profit_factor_cost_005": profit_factor(net_005),
        "max_drawdown_r_cost_005": max_drawdown(net_005),
        "avg_minutes_held": round(float(np.mean([t["minutes_held"] for t in trades])), 2),
        "median_bars_to_05r": float(np.median(half_bars)) if half_bars else None,
    }


def _summarize_variant(trades: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "all": _metrics(trades),
        "buy": _metrics([t for t in trades if t["side"] == "BUY"]),
        "sell": _metrics([t for t in trades if t["side"] == "SELL"]),
    }


def run_research(
    frame: pd.DataFrame,
    symbol: str,
    label: str,
    requested_bars: int,
    start: str | None = None,
    end: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    prepared = prepare_bars(frame)
    fetched_bars = len(prepared)
    fetched_first = prepared.index.min().isoformat()
    fetched_last = prepared.index.max().isoformat()
    # Apply requested output dates only after indicators have used all earlier history.
    if start:
        prepared = prepared.loc[prepared.index >= pd.Timestamp(start, tz="UTC")]
    if end:
        prepared = prepared.loc[prepared.index < pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)]
    if prepared.empty:
        raise ValueError("No candles fall inside the requested date range")

    report: dict[str, Any] = {
        "symbol": symbol,
        "label": label,
        "signal_timeframe": "5m",
        "execution_timeframe": "5m; entries use the next candle open",
        "timezone": NY,
        "session_reference": "New York 09:30; BTC uses a synthetic NY session because crypto trades 24/7",
        "premarket_window": "04:00-09:30 New York time",
        "opening_range": "09:30-09:35 New York time (first completed 5m candle)",
        "entry_window": "09:35-15:45 New York time",
        "force_flat": "15:55 New York time",
        "daily_bias": "Previous completed New York calendar day close vs EMA20/EMA50",
        "a_plus_filter": "Prior-day bias and intraday EMA20/EMA50+ADX18 direction agree; signal candle body/range >= 0.40",
        "risk_model": "SL beyond the retest/candle structure with 0.10x 5m ATR buffer; TP=2R",
        "same_candle_sl_tp": "SL takes precedence (conservative)",
        "quick_failure_test": f"Exit after {QUICK_FAILURE_BARS} bars if maximum favorable excursion remains below {QUICK_FAILURE_MFE_R:.2f}R",
        "cost_sensitivity": {"0.00R": "no cost assumption", "0.05R": "assumed round trip cost per trade", "0.10R": "assumed round trip cost per trade"},
        "data": {
            "requested_bars": int(requested_bars),
            "fetched_bars": int(fetched_bars),
            "analysis_bars_after_date_filter": int(len(prepared)),
            "history_partial": fetched_bars < requested_bars,
            "fetched_first_bar_utc": fetched_first,
            "fetched_last_bar_utc": fetched_last,
            "first_bar_utc": prepared.index.min().isoformat(),
            "last_bar_utc": prepared.index.max().isoformat(),
            "rows_per_new_york_day": int(prepared.groupby("ny_date").size().median()),
        },
        "strategies": {},
        "warning": (
            "Research only. No production signal, threshold, risk, or execution setting is changed. "
            "5m data is used because the configured market provider does not expose a 1m feed here. "
            "The 09:30 BTC window is a time-of-day proxy, not an actual exchange open. "
            "Costs are assumptions, not measured broker fees."
        ),
    }

    all_trades: list[dict[str, Any]] = []
    for strategy in STRATEGIES:
        candidates = _generate_candidates_global(prepared, strategy)
        variant_trades: dict[str, list[dict[str, Any]]] = {v: [] for v in VARIANTS}
        unresolved: dict[str, int] = defaultdict(int)
        for variant in VARIANTS:
            accepted = [
                c for c in candidates
                if variant == "base" or _a_plus(c, prepared)
            ]
            quick = variant == "a_plus_fast_exit"
            # A single position per strategy+variant; ignore signals while it is open.
            next_available = -1
            for candidate in accepted:
                if int(candidate["entry_i"]) <= next_available:
                    continue
                trade = _simulate_trade(prepared, candidate, quick_failure=quick)
                if trade is None:
                    unresolved[variant] += 1
                    continue
                trade["symbol"] = symbol
                trade["variant"] = variant
                variant_trades[variant].append(trade)
                next_available = int(prepared.index.get_indexer([pd.Timestamp(trade["exit_time_utc"])])[0])
                all_trades.append(trade)
        report["strategies"][strategy] = {
            "label": STRATEGY_LABELS[strategy],
            "candidate_setups": len(candidates),
            "variants": {
                variant: {
                    **_summarize_variant(variant_trades[variant]),
                    "unresolved_at_data_end": int(unresolved[variant]),
                }
                for variant in VARIANTS
            },
        }
    return report, all_trades


def _load_market(symbol: str, bars: int) -> pd.DataFrame:
    from market import fetch
    frame = fetch(symbol, "5m", period=bars)
    # market.fetch timestamps bars by their opening time; remove an unfinished candle.
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(minutes=BAR_MINUTES)
    frame = frame.loc[frame.index + pd.Timedelta(minutes=BAR_MINUTES) <= cutoff].copy()
    if len(frame) < 100:
        raise RuntimeError(f"{symbol}: only {len(frame)} completed 5m candles were returned")
    return frame


def main() -> None:
    parser = argparse.ArgumentParser(description="Research three New York-session setups on Gold and Bitcoin")
    parser.add_argument("--bars", type=int, default=30000, help="5m candles requested per market (default: 30000)")
    parser.add_argument("--start", default=None, help="Optional inclusive UTC date, YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="Optional inclusive UTC date, YYYY-MM-DD")
    parser.add_argument("--out-dir", default="research/results")
    args = parser.parse_args()
    if args.bars < 500:
        parser.error("--bars must be at least 500")

    from config import ASSETS
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "requested_bars_per_market": args.bars,
        "markets": {},
    }
    for key in ("GOLD", "BTC"):
        asset = ASSETS[key]
        symbol = asset["symbol"]
        try:
            print(f"Fetching {asset['label']} ({symbol}) 5m OHLC...", flush=True)
            frame = _load_market(symbol, args.bars)
            market_report, trades = run_research(
                frame, symbol, asset["label"], args.bars,
                start=args.start, end=args.end,
            )
            market_report["provider"] = frame.attrs.get("market_data_source", "source not reported")
            market_report["provider_warning"] = frame.attrs.get("market_data_warning")
            report["markets"][key] = market_report
            pd.DataFrame(trades).to_csv(out_dir / f"seven_rules_{key.lower()}_trades.csv", index=False)
            print(f"{asset['label']}: completed; {len(trades)} variant rows exported", flush=True)
        except Exception as exc:
            report["markets"][key] = {
                "symbol": symbol, "label": asset["label"],
                "error": f"{type(exc).__name__}: {exc}",
            }
            print(f"{asset['label']}: FAILED: {type(exc).__name__}: {exc}", flush=True)

    summary_path = out_dir / "seven_rules_summary.json"
    summary_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), flush=True)
    if all("error" in v for v in report["markets"].values()):
        raise SystemExit("Research failed for both markets; see market error messages")


if __name__ == "__main__":
    main()
