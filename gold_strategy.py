"""Pure gold-session strategy helpers.

This module is intentionally side-effect free. It provides reusable building
blocks for a London-session breakout design without changing the live signal
pipeline until it has been validated with historical data.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd


@dataclass(frozen=True)
class SessionRange:
    high: float
    low: float
    bars: int

    @property
    def width(self) -> float:
        return self.high - self.low


def asian_range(df: pd.DataFrame, start_hour: int = 0, end_hour: int = 7) -> Optional[SessionRange]:
    """Build a UTC session range from completed OHLC bars.

    The index must be timezone-aware. The end hour is exclusive. ``None`` is
    returned when no valid bars exist or the range is degenerate.
    """
    if df is None or df.empty or not isinstance(df.index, pd.DatetimeIndex):
        return None
    idx = df.index
    if idx.tz is None:
        raise ValueError("gold_strategy requires a timezone-aware index")
    hours = idx.hour
    mask = (hours >= int(start_hour)) & (hours < int(end_hour))
    part = df.loc[mask]
    if part.empty:
        return None
    high = float(part["High"].max())
    low = float(part["Low"].min())
    if not high > low:
        return None
    return SessionRange(high=high, low=low, bars=len(part))


def breakout_direction(close: float, session: SessionRange, buffer: float = 0.0) -> Optional[str]:
    """Return BUY/SELL only after a close breaks the session range."""
    if close > session.high + float(buffer):
        return "BUY"
    if close < session.low - float(buffer):
        return "SELL"
    return None


def momentum_confirmed(row: pd.Series, direction: str) -> bool:
    """Confirm direction with RSI and MACD histogram when available."""
    try:
        rsi = float(row["rsi"])
        macd = float(row["macd_hist"])
    except (KeyError, TypeError, ValueError):
        return False
    if direction == "BUY":
        return rsi >= 50.0 and macd > 0.0
    if direction == "SELL":
        return rsi <= 50.0 and macd < 0.0
    return False


def atr_levels(entry: float, direction: str, atr: float, multiple: float = 1.15, rr: float = 2.0):
    """Calculate non-negative ATR-based SL/TP levels."""
    entry, atr = float(entry), float(atr)
    if atr <= 0 or rr <= 0 or multiple <= 0:
        raise ValueError("atr, multiple and rr must be positive")
    risk = atr * multiple
    if direction == "BUY":
        sl, tp = entry - risk, entry + risk * rr
    elif direction == "SELL":
        sl, tp = entry + risk, entry - risk * rr
    else:
        raise ValueError("direction must be BUY or SELL")
    return round(sl, 2), round(tp, 2)
