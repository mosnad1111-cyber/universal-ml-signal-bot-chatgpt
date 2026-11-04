"""Prepare decision-only inputs for the standalone LEAN worker.

The XGBoost walk-forward model runs here, using the same code and market-data
providers as the existing backtest. Only raw completed candles and accepted
setup levels are exported; Python-resolved trade outcomes are never sent to LEAN.
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backtest import GoldBacktest
from config import (
    ASSETS,
    BACKTEST_BARS_5M,
    BACKTEST_BARS_15M,
    BACKTEST_BARS_1H,
    BACKTEST_RETRAIN_EVERY,
)


TIMEFRAME_BARS = {
    "5m": BACKTEST_BARS_5M,
    "15m": BACKTEST_BARS_15M,
    "1h": BACKTEST_BARS_1H,
}
PERIOD_MINUTES = {"5m": 5, "15m": 15, "1h": 60}


def _utc_naive(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _write_timeframe(output: Path, asset_key: str, tf: str, result: dict, request_id: str):
    tf_dir = output / tf
    bars_dir = tf_dir / "bars"
    bars_dir.mkdir(parents=True, exist_ok=True)

    grouped: dict[str, list[dict]] = {}
    for bar in result["market_bars"]:
        stamp = _utc_naive(bar["time"])
        day = stamp.strftime("%Y%m%d")
        grouped.setdefault(day, []).append({
            "time": stamp.strftime("%Y%m%d %H:%M:%S"),
            "open": bar["open"],
            "high": bar["high"],
            "low": bar["low"],
            "close": bar["close"],
            "volume": bar["volume"],
            "period_minutes": PERIOD_MINUTES[tf],
        })

    if not result.get("market_bars"):
        raise RuntimeError(f"{asset_key} {tf}: no raw historical OHLC bars were exported")
    raw_start = _utc_naive(result["market_bars"][0]["time"])
    raw_end = _utc_naive(result["market_bars"][-1]["time"])
    first_day = raw_start.date()
    last_day = raw_end.date()
    day_cursor = first_day
    # LEAN can request the UTC partition immediately after the final snapshot
    # date while advancing its end-of-day frontier. Write an empty CSV for that
    # boundary day instead of letting a missing file abort the entire feed.
    final_partition_day = last_day + timedelta(days=1)
    while day_cursor <= final_partition_day:
        day = day_cursor.strftime("%Y%m%d")
        rows = grouped.get(day, [])
        with (bars_dir / f"{day}.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=("time", "open", "high", "low", "close", "volume", "period_minutes"),
            )
            writer.writeheader()
            writer.writerows(rows)
        day_cursor += timedelta(days=1)

    market_data_source = str(result.get("market_data_source") or "").strip()
    if not market_data_source:
        raise RuntimeError(f"{asset_key} {tf}: market data provider was not identified")

    metadata = {
        "request_id": request_id,
        "asset_key": asset_key,
        "symbol": result["symbol"],
        "asset_label": result["asset_label"],
        "timeframe": tf,
        "period_minutes": PERIOD_MINUTES[tf],
        # LEAN must ingest the complete raw snapshot, including the warm-up bars
        # before the first feature/prediction timestamp.
        "start": raw_start.isoformat(),
        "end": raw_end.isoformat(),
        "snapshot_start": result["snapshot_start"],
        "snapshot_end": result["snapshot_end"],
        "fee_r": 0.05,
        "bars_count": len(result["market_bars"]),
        "requested_bars": int(result.get("requested_bars", len(result["market_bars"]))),
        "raw_bars": int(result.get("raw_bars", len(result["market_bars"]))),
        "completed_bars": int(result.get("completed_bars", len(result["market_bars"]))),
        "usable_bars": int(result.get("usable_bars", 0)),
        "history_partial": bool(result.get("history_partial", False)),
        "signals_count": len(result["lean_events"]),
        "data_source": "real historical OHLC from the configured market-data provider",
        "market_data_source": market_data_source,
        "volume_note": (
            "Gold volume is the existing neutral proxy and is not treated as real traded volume."
            if asset_key == "GOLD" else str(result.get("market_data_warning") or "Binance/TradingView historical volume; provider is identified above."),
        ),
    }
    (tf_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (tf_dir / "signals.json").write_text(
        json.dumps(result["lean_events"], ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    baseline = {
        key: result.get(key)
        for key in (
            "timeframe", "symbol", "asset_label", "requested_bars", "raw_bars",
            "completed_bars", "usable_bars", "history_partial", "snapshot_id",
            "snapshot_start", "snapshot_end", "trades", "wins", "losses",
            "targets", "stops", "timeouts", "ambiguous_entry_candle", "net_r",
            "win_rate", "profit_factor", "max_drawdown_r", "expectancy_r",
            "trades_detail",
        )
    }
    (tf_dir / "python_baseline.json").write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return metadata


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset", choices=tuple(ASSETS), required=True)
    parser.add_argument("--timeframes", default="5m,15m,1h")
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--output-dir", default="lean-input")
    args = parser.parse_args()

    asset = ASSETS[args.asset]
    timeframes = [item.strip() for item in args.timeframes.split(",") if item.strip()]
    unknown = sorted(set(timeframes) - set(TIMEFRAME_BARS))
    if not timeframes or unknown:
        raise SystemExit(f"Unsupported timeframes: {unknown or timeframes}")

    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    summaries = {}
    backtest = GoldBacktest()

    for tf in timeframes:
        requested = TIMEFRAME_BARS[tf]
        print(f"[prepare] {args.asset} {tf}: requested={requested}", flush=True)
        result = backtest.run(
            tf,
            bars=requested,
            retrain_every=BACKTEST_RETRAIN_EVERY,
            symbol=asset["symbol"],
            include_all_trades=True,
            include_lean_payload=True,
        )
        if result.get("error"):
            raise RuntimeError(f"{args.asset} {tf} preparation failed: {result['error']}")
        metadata = _write_timeframe(output, args.asset, tf, result, args.request_id)
        summaries[tf] = {
            "metadata": metadata,
            "python_baseline": {
                key: result.get(key)
                for key in ("trades", "wins", "losses", "net_r", "win_rate",
                            "profit_factor", "max_drawdown_r", "expectancy_r")
            },
        }
        print(
            f"[prepare] {tf}: bars={metadata['bars_count']} "
            f"signals={metadata['signals_count']} "
            f"python_trades={result.get('trades', 0)}",
            flush=True,
        )

    (output / "prepared_summary.json").write_text(
        json.dumps({
            "request_id": args.request_id,
            "asset_key": args.asset,
            "timeframes": summaries,
        }, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
