"""Historical backtest for the London-breakout gold strategy.

Run locally with: python strategy_backtest.py 5m 5000
This is intentionally separate from the live Telegram engine until results
have been reviewed. It uses the repository's market data and feature code.
"""
from __future__ import annotations

import json
import sys
from datetime import timedelta

from config import GOLD_DATA_SYMBOL, RR, SL_ATR_MULT, TIMEFRAMES, BACKTEST_BARS
from market import fetch, add_features
from gold_strategy import asian_range, breakout_direction, momentum_confirmed, atr_levels


def run(tf: str, bars: int | None = None) -> dict:
    requested = int(bars or BACKTEST_BARS)
    raw = fetch(GOLD_DATA_SYMBOL, tf, period=requested)
    if len(raw) < 300:
        return {"timeframe": tf, "error": f"insufficient_data:{len(raw)}", "bars": len(raw)}
    completed = raw.iloc[:-1].copy()
    x = add_features(completed).dropna()
    trades = []
    blocked = {"no_breakout": 0, "momentum": 0}
    for i in range(1, len(x) - 1):
        row = x.iloc[i]
        ts = x.index[i]
        day = ts.normalize()
        session = asian_range(completed.loc[(completed.index >= day) & (completed.index < day + timedelta(hours=7))])
        if session is None:
            continue
        direction = breakout_direction(float(row["Close"]), session, buffer=0.0)
        if direction is None:
            blocked["no_breakout"] += 1
            continue
        if not momentum_confirmed(row, direction):
            blocked["momentum"] += 1
            continue
        entry = float(row["Close"])
        sl, tp = atr_levels(entry, direction, float(row["atr"]), SL_ATR_MULT, RR)
        result = None
        for j in range(i + 1, len(completed)):
            hi, lo = float(completed["High"].iloc[j]), float(completed["Low"].iloc[j])
            if direction == "BUY":
                hit_sl, hit_tp = lo <= sl, hi >= tp
            else:
                hit_sl, hit_tp = hi >= sl, lo <= tp
            if hit_sl or hit_tp:
                result = "SL" if hit_sl else "TP"
                break
        if result:
            trades.append({"time": ts.isoformat(), "direction": direction, "entry": entry, "sl": sl, "tp": tp, "result": result, "r": -1.0 if result == "SL" else RR})
    wins = sum(t["result"] == "TP" for t in trades)
    losses = len(trades) - wins
    net = sum(t["r"] for t in trades)
    return {"timeframe": tf, "requested_bars": requested, "raw_bars": len(raw), "usable_bars": len(x), "trades": len(trades), "wins": wins, "losses": losses, "win_rate": round(100 * wins / len(trades), 2) if trades else 0.0, "net_r": round(net, 2), "expectancy_r": round(net / len(trades), 4) if trades else 0.0, "filters": blocked, "trades_detail": trades[-100:]}


if __name__ == "__main__":
    tf = sys.argv[1] if len(sys.argv) > 1 else TIMEFRAMES[0]
    bars = int(sys.argv[2]) if len(sys.argv) > 2 else BACKTEST_BARS
    print(json.dumps(run(tf, bars), ensure_ascii=False, indent=2))
