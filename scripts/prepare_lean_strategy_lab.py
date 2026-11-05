"""Prepare decision-only strategy hypotheses for independent LEAN execution.

XGBoost walk-forward predictions and setup decisions are generated chronologically
per strategy. Python does not resolve trades; LEAN owns every fill and exit.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from backtest import GoldBacktest
from config import (
    ASSETS,
    BACKTEST_BARS_5M,
    BACKTEST_BARS_15M,
    BACKTEST_BARS_1H,
    BACKTEST_RETRAIN_EVERY,
)
from strategy_lab import STRATEGIES, STRATEGY_NAMES


TIMEFRAME_BARS = {
    "5m": max(10000, BACKTEST_BARS_5M),
    "15m": max(10000, BACKTEST_BARS_15M),
    "1h": max(10000, BACKTEST_BARS_1H),
}
PERIOD_MINUTES = {"5m": 5, "15m": 15, "1h": 60}


def _utc_timestamp(value) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        return stamp.tz_localize("UTC")
    return stamp.tz_convert("UTC")


def _utc_iso(value) -> str:
    return _utc_timestamp(value).isoformat()


def _lean_capital_requirements(raw: pd.DataFrame) -> tuple[float, float]:
    """Size LEAN's cash buffer for one fixed unit at the highest historical OHLC price.

    Strategy execution remains one unit per entry. The account cash is only the
    buying-power envelope required to let LEAN validate that unit at every
    historical price, with 50% headroom; it does not change signal size.
    """
    if raw.empty:
        raise RuntimeError("Cannot size LEAN buying power from empty OHLC history.")
    columns = ("Open", "High", "Low", "Close")
    missing = [column for column in columns if column not in raw.columns]
    if missing:
        raise RuntimeError(f"Cannot size LEAN buying power; OHLC columns missing: {missing}")
    values = pd.concat([pd.to_numeric(raw[column], errors="coerce") for column in columns])
    values = values[values.map(math.isfinite)]
    if values.empty or (values <= 0).any():
        raise RuntimeError("Cannot size LEAN buying power from invalid or non-positive OHLC prices.")
    max_price = float(values.max())
    required_cash = max(100_000.0, max_price * 1.5)
    initial_cash = float(math.ceil(required_cash / 1000.0) * 1000)
    return max_price, initial_cash


def _write_bars(tf_dir: Path, raw: pd.DataFrame) -> tuple[int, dict[str, int]]:
    if raw.empty:
        raise RuntimeError(f"{tf_dir.name}: no raw historical OHLC bars.")
    stamps = [_utc_timestamp(value) for value in raw.index]
    if any(right <= left for left, right in zip(stamps, stamps[1:])):
        raise RuntimeError(f"{tf_dir.name}: raw OHLC timestamps are not strictly increasing.")
    serialized = [stamp.strftime("%Y-%m-%d %H:%M:%S.%f") for stamp in stamps]
    if len(set(serialized)) != len(serialized):
        raise RuntimeError(f"{tf_dir.name}: duplicate timestamps in raw OHLC history.")

    daily = Counter(stamp.strftime("%Y%m%d") for stamp in stamps)
    bars_path = tf_dir / "bars.csv"
    with bars_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("time", "open", "high", "low", "close", "volume", "period_minutes"),
        )
        writer.writeheader()
        for stamp_text, (_, bar) in zip(serialized, raw.iterrows()):
            writer.writerow({
                "time": stamp_text,
                "open": float(bar["Open"]),
                "high": float(bar["High"]),
                "low": float(bar["Low"]),
                "close": float(bar["Close"]),
                "volume": float(bar.get("Volume", 0.0)),
                "period_minutes": PERIOD_MINUTES[tf_dir.name],
            })
    return len(serialized), dict(sorted(daily.items()))


def _write_strategy(
    tf_dir: Path,
    strategy: str,
    result: dict,
    common: dict,
) -> dict:
    if result.get("decision_only") is not True:
        raise RuntimeError(f"{tf_dir.name}/{strategy}: Python trade resolution was not disabled.")
    signals = result.get("lean_events")
    if not isinstance(signals, list):
        raise RuntimeError(f"{tf_dir.name}/{strategy}: LEAN decision events are missing.")

    bar_times = set()
    with (tf_dir / "bars.csv").open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            bar_times.add(row["time"])
    signal_times = [
        _utc_timestamp(event["signal_time"]).strftime("%Y-%m-%d %H:%M:%S.%f")
        for event in signals
    ]
    missing = sorted(set(signal_times) - bar_times)
    if missing:
        raise RuntimeError(
            f"{tf_dir.name}/{strategy}: decision timestamps do not match original OHLC: {missing[:5]}"
        )
    if signal_times != sorted(signal_times) or len(signal_times) != len(set(signal_times)):
        raise RuntimeError(f"{tf_dir.name}/{strategy}: strategy decision timestamps are not unique and ordered.")

    strategy_dir = tf_dir / "strategies" / strategy
    strategy_dir.mkdir(parents=True, exist_ok=True)
    metadata = {
        **common,
        "strategy": strategy,
        "strategy_label": STRATEGY_NAMES[strategy],
        "fee_r": 0.05,
        "signals_count": len(signals),
        "signals_aligned_to_ohlc": True,
        "decision_only": True,
        "setup_candidates": int(result.get("setup_candidates", len(signals))),
        "model_fit_diagnostics": result.get("model_fit_diagnostics", {}),
        "rejection_counts": result.get("rejection_counts", {}),
        "direction_rejections": result.get("direction_rejections", {}),
    }
    (strategy_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    (strategy_dir / "signals.json").write_text(
        json.dumps(signals, ensure_ascii=False, separators=(",", ":"), allow_nan=False),
        encoding="utf-8",
    )
    return {"status": "ready", "metadata": metadata}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset", choices=tuple(ASSETS), required=True)
    parser.add_argument("--timeframes", default="5m,15m,1h")
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--output-dir", default="lean-strategy-lab-input")
    args = parser.parse_args()

    if len(args.request_id) != 32 or any(ch not in "0123456789abcdef" for ch in args.request_id):
        raise SystemExit("Invalid request ID.")
    asset = ASSETS[args.asset]
    timeframes = [part.strip() for part in args.timeframes.split(",") if part.strip()]
    unknown = sorted(set(timeframes) - set(TIMEFRAME_BARS))
    if not timeframes or unknown or len(timeframes) != len(set(timeframes)):
        raise SystemExit(f"Unsupported or duplicate timeframes: {unknown or timeframes}")

    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    backtest = GoldBacktest()
    cutoff = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    print(f"[strategy-lab] LEAN exclusive UTC cutoff: {cutoff.isoformat()}", flush=True)

    summaries = {}
    for tf in timeframes:
        requested = TIMEFRAME_BARS[tf]
        prepared = backtest._prepare(
            tf, requested, symbol=asset["symbol"], end_before=cutoff
        )
        raw = prepared["raw"]
        features = prepared["features"]
        if len(features) < 1200:
            raise RuntimeError(f"{args.asset} {tf}: insufficient usable candles ({len(features)}).")

        tf_dir = output / tf
        tf_dir.mkdir(parents=True, exist_ok=True)
        bars_count, daily_counts = _write_bars(tf_dir, raw)
        first_feature = _utc_timestamp(features.index[0])
        last_feature = _utc_timestamp(features.index[-1])
        midpoint = first_feature + (last_feature - first_feature) / 2
        raw_start = _utc_timestamp(raw.index[0])
        raw_end = _utc_timestamp(raw.index[-1])
        source = str(prepared.get("market_data_source") or "").strip()
        if not source or "synthetic" in source.lower():
            raise RuntimeError(f"{args.asset} {tf}: historical provider is not identified as real data.")
        max_ohlc_price, lean_initial_cash_usd = _lean_capital_requirements(raw)

        common = {
            "request_id": args.request_id,
            "asset_key": args.asset,
            "symbol": asset["symbol"],
            "asset_label": asset["label"],
            "timeframe": tf,
            "period_minutes": PERIOD_MINUTES[tf],
            "start": raw_start.isoformat(),
            "end": raw_end.isoformat(),
            "snapshot_start": first_feature.isoformat(),
            "snapshot_end": last_feature.isoformat(),
            "midpoint": midpoint.isoformat(),
            "fee_r": 0.05,
            "bars_count": bars_count,
            "bars_file": "bars.csv",
            "max_ohlc_price": max_ohlc_price,
            "lean_initial_cash_usd": lean_initial_cash_usd,
            "lean_position_units": 1.0,
            "lean_cash_buffer_multiplier": 1.5,
            "raw_bars": int(prepared.get("received_bars", len(raw))),
            "completed_bars": len(raw),
            "usable_bars": len(features),
            "requested_bars": int(prepared.get("requested_bars", requested)),
            "history_partial": bool(prepared.get("history_partial", False)),
            "end_before_exclusive_utc": prepared.get("end_before_exclusive_utc"),
            "end_window_excluded_bars": int(prepared.get("end_window_excluded_bars", 0)),
            "daily_bar_counts": daily_counts,
            "market_data_source": source,
            "market_data_type": "real_historical_ohlc",
            "signals_aligned_to_ohlc": True,
            "volume_note": (
                "Gold volume is a neutral proxy and is not real traded volume."
                if args.asset == "GOLD"
                else str(prepared.get("market_data_warning") or "Provider volume is reported as supplied.")
            ),
        }
        results = {}
        for strategy in STRATEGIES:
            print(f"[strategy-lab] {args.asset} {tf} {strategy}: walk-forward decisions", flush=True)
            result = backtest.run(
                tf,
                bars=requested,
                retrain_every=BACKTEST_RETRAIN_EVERY,
                _prepared=prepared,
                symbol=asset["symbol"],
                strategy=strategy,
                include_lean_payload=True,
                decision_only=True,
                end_before=cutoff,
            )
            if result.get("error"):
                results[strategy] = {
                    "status": "not_testable",
                    "error": str(result["error"]),
                    "model_fit_diagnostics": result.get("model_fit_diagnostics", {}),
                }
                print(f"[strategy-lab] {tf} {strategy}: not_testable: {result['error']}", flush=True)
                continue
            results[strategy] = _write_strategy(tf_dir, strategy, result, common)
            print(
                f"[strategy-lab] {tf} {strategy}: decisions={results[strategy]['metadata']['signals_count']} "
                f"fit_attempts={results[strategy]['metadata']['model_fit_diagnostics'].get('fit_attempts', 0)}",
                flush=True,
            )

        if not any(item.get("status") == "ready" for item in results.values()):
            raise RuntimeError(f"{args.asset} {tf}: no strategy produced a valid LEAN decision stream.")
        (tf_dir / "metadata.json").write_text(
            json.dumps(common, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        summaries[tf] = {"metadata": common, "strategies": results}

    (output / "prepared_summary.json").write_text(
        json.dumps({
            "request_id": args.request_id,
            "asset_key": args.asset,
            "strategy_order": list(STRATEGIES),
            "timeframes": summaries,
        }, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
