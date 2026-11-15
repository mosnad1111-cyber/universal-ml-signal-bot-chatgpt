"""Prepare every strategy x Score/AI candidate for independent LEAN execution.

Each strategy owns one chronological Walk-Forward prediction stream per asset
and timeframe. The full threshold grid is applied to that stream without
retraining; every accepted decision set is independently replayed by LEAN.
Python never resolves fills, exits, or trade outcomes.
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
    GRID_AI_PROBS,
    GRID_SCORES,
    thresholds_for,
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


def threshold_id(score: float, ai: float) -> str:
    return f"score_{int(round(float(score))):03d}_ai_{int(round(float(ai) * 100)):03d}"


def threshold_axes(timeframe: str) -> tuple[list[float], list[float], float, float]:
    """Return the full configured grid plus this timeframe's live threshold pair."""
    live_score, live_ai = thresholds_for(timeframe)
    scores = sorted(set(float(value) for value in GRID_SCORES) | {float(live_score)})
    ai_values = sorted(set(float(value) for value in GRID_AI_PROBS) | {float(live_ai)})
    return scores, ai_values, float(live_score), float(live_ai)


def _lean_capital_requirements(raw: pd.DataFrame) -> tuple[float, float]:
    """Size LEAN cash for one fixed unit at the highest observed OHLC price."""
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


def _write_candidate(
    tf_dir: Path,
    strategy: str,
    result: dict,
    common: dict,
    score: float,
    ai: float,
    live_score: float,
    live_ai: float,
) -> dict:
    if result.get("decision_only") is not True:
        raise RuntimeError(f"{tf_dir.name}/{strategy}: Python trade resolution was not disabled.")
    signals = result.get("lean_events")
    if not isinstance(signals, list):
        raise RuntimeError(f"{tf_dir.name}/{strategy}: LEAN decision events are missing.")

    bar_times = set()
    with (tf_dir / "bars.csv").open(newline="", encoding="utf-8") as handle:
        bar_times = {row["time"] for row in csv.DictReader(handle)}
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
        raise RuntimeError(f"{tf_dir.name}/{strategy}: decisions are not unique and ordered.")

    candidate = threshold_id(score, ai)
    candidate_key = f"{strategy}__{candidate}"
    candidate_dir = tf_dir / "strategies" / strategy / "candidates" / candidate
    candidate_dir.mkdir(parents=True, exist_ok=True)
    is_live_pair = (
        abs(float(score) - float(live_score)) < 1e-9
        and abs(float(ai) - float(live_ai)) < 1e-9
    )
    metadata = {
        **common,
        "strategy": candidate_key,
        "strategy_type": strategy,
        "strategy_name": STRATEGY_NAMES[strategy],
        "candidate_id": candidate_key,
        "threshold_id": candidate,
        "strategy_label": f"{STRATEGY_NAMES[strategy]} | Score ≥ {score:g} | AI ≥ {ai * 100:.0f}%",
        "score_threshold": float(score),
        "ai_threshold": float(ai),
        "is_live_threshold_pair": bool(is_live_pair),
        "is_live_baseline": bool(strategy == "baseline" and is_live_pair),
        "signals_count": len(signals),
        "signals_aligned_to_ohlc": True,
        "decision_only": True,
        "setup_candidates": int(result.get("setup_candidates", len(signals))),
        "model_fit_diagnostics": result.get("model_fit_diagnostics", {}),
        "rejection_counts": result.get("rejection_counts", {}),
        "direction_rejections": result.get("direction_rejections", {}),
    }
    (candidate_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (candidate_dir / "signals.json").write_text(
        json.dumps(signals, ensure_ascii=False, separators=(",", ":"), allow_nan=False),
        encoding="utf-8",
    )
    return {
        "status": "ready",
        "candidate_id": candidate_key,
        "threshold_id": candidate,
        "metadata": metadata,
    }


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
        scores, ai_values, live_score, live_ai = threshold_axes(tf)
        threshold_candidates = [
            (score, ai, threshold_id(score, ai))
            for score in scores
            for ai in ai_values
        ]

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
            "active_thresholds": {"score": live_score, "ai": live_ai},
            "score_thresholds": scores,
            "ai_thresholds": ai_values,
            "threshold_count_per_strategy": len(threshold_candidates),
            "candidate_count_expected": len(STRATEGIES) * len(threshold_candidates),
            "volume_note": (
                "Gold volume is a neutral proxy and is not real traded volume."
                if args.asset == "GOLD"
                else str(prepared.get("market_data_warning") or "Provider volume is reported as supplied.")
            ),
        }

        strategy_results = {}
        for strategy in STRATEGIES:
            print(
                f"[strategy-lab] {args.asset} {tf} {strategy}: one strategy-specific "
                f"Walk-Forward stream, then {len(threshold_candidates)} threshold pairs",
                flush=True,
            )
            predictions = backtest._walk_forward_predictions(
                tf, features, BACKTEST_RETRAIN_EVERY, asset["symbol"], strategy
            )
            diagnostics = predictions.get("_diagnostics", {}) if isinstance(predictions, dict) else {}
            prediction_count = (
                sum(isinstance(key, int) for key in predictions)
                if isinstance(predictions, dict) else 0
            )
            if prediction_count == 0:
                reason = (
                    f"no_walk_forward_predictions_for_strategy:{strategy}; "
                    f"fit_failures={diagnostics.get('fit_failures', 0)}; "
                    f"last_fit_error={diagnostics.get('last_fit_error')}"
                )
                strategy_results[strategy] = {
                    "status": "not_testable",
                    "error": reason,
                    "model_fit_diagnostics": diagnostics,
                    "candidate_order": [f"{strategy}__{candidate}" for _, _, candidate in threshold_candidates],
                    "candidates": {
                        f"{strategy}__{candidate}": {
                            "status": "not_testable",
                            "error": reason,
                            "threshold_id": candidate,
                            "score_threshold": score,
                            "ai_threshold": ai,
                        }
                        for score, ai, candidate in threshold_candidates
                    },
                }
                print(f"[strategy-lab] {tf} {strategy}: not_testable: {reason}", flush=True)
                continue

            candidate_results = {}
            candidate_order = []
            for index, (score, ai, candidate) in enumerate(threshold_candidates, 1):
                candidate_key = f"{strategy}__{candidate}"
                candidate_order.append(candidate_key)
                print(
                    f"[strategy-lab] {args.asset} {tf} {strategy} "
                    f"{index}/{len(threshold_candidates)}: {candidate}",
                    flush=True,
                )
                result = backtest.run(
                    tf,
                    bars=requested,
                    retrain_every=BACKTEST_RETRAIN_EVERY,
                    min_score_override=score,
                    min_ai_override=ai,
                    _prepared=prepared,
                    _predictions=predictions,
                    symbol=asset["symbol"],
                    strategy=strategy,
                    include_lean_payload=True,
                    decision_only=True,
                    end_before=cutoff,
                )
                if result.get("error"):
                    candidate_results[candidate_key] = {
                        "status": "not_testable",
                        "error": str(result["error"]),
                        "candidate_id": candidate_key,
                        "threshold_id": candidate,
                        "score_threshold": score,
                        "ai_threshold": ai,
                    }
                    continue
                written = _write_candidate(
                    tf_dir, strategy, result, common, score, ai, live_score, live_ai
                )
                candidate_results[candidate_key] = written

            ready = sum(item.get("status") == "ready" for item in candidate_results.values())
            if ready == 0:
                strategy_results[strategy] = {
                    "status": "not_testable",
                    "error": "no_threshold_candidate_produced_valid_decisions",
                    "model_fit_diagnostics": diagnostics,
                    "candidate_order": candidate_order,
                    "candidates": candidate_results,
                }
            else:
                strategy_results[strategy] = {
                    "status": "ready",
                    "model_fit_diagnostics": diagnostics,
                    "candidate_order": candidate_order,
                    "candidate_count": len(candidate_order),
                    "ready_candidates": ready,
                    "candidates": candidate_results,
                }

        if not any(item.get("status") == "ready" for item in strategy_results.values()):
            raise RuntimeError(f"{args.ASSET} {tf}: no strategy/threshold candidate produced LEAN inputs.")
        (tf_dir / "metadata.json").write_text(
            json.dumps(common, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        summaries[tf] = {
            "metadata": common,
            "strategy_order": list(STRATEGIES),
            "scores": scores,
            "ai_probabilities": ai_values,
            "threshold_order": [candidate for _, _, candidate in threshold_candidates],
            "strategies": strategy_results,
        }

    if not summaries:
        raise SystemExit("No timeframe was prepared for the combined strategy/threshold lab.")
    (output / "prepared_summary.json").write_text(
        json.dumps({
            "report_type": "LEAN real historical strategy-threshold lab",
            "market_data_type": "real_historical_ohlc",
            "synthetic_fixture": False,
            "request_id": args.request_id,
            "asset_key": args.asset,
            "strategy_order": list(STRATEGIES),
            "strategy_names": STRATEGY_NAMES,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "timeframes": summaries,
            "experiment_design": (
                "One real historical OHLC snapshot is shared by every candidate within an asset/timeframe. "
                "Each strategy generates its own single chronological Walk-Forward prediction stream. "
                "All Score/AI threshold pairs are then applied to that same strategy-specific stream, "
                "without retraining the model for each pair. Python exports decisions only; every "
                "strategy/threshold pair is independently replayed by LEAN, which alone resolves entries, "
                "fills, exits, and fees. Candidate selection is based on Calibration only; Holdout is "
                "kept independent and is never used to select a strategy or threshold. The operational "
                "live threshold pair is included as the baseline for the current baseline strategy."
            ),
        }, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
