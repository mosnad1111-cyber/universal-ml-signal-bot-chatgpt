"""Prepare score/AI threshold candidates for independent LEAN execution.

XGBoost walk-forward probabilities and setup decisions are computed once per
asset/timeframe snapshot. Python only exports accepted decision events: LEAN
alone resolves entries, fills, exits, fees, and the completed trade ledger.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtest import GoldBacktest
from config import ASSETS, BACKTEST_RETRAIN_EVERY, GRID_AI_PROBS, GRID_SCORES, thresholds_for
from scripts.prepare_lean_strategy_lab import (
    PERIOD_MINUTES,
    TIMEFRAME_BARS,
    _lean_capital_requirements,
    _utc_timestamp,
    _write_bars,
)


def threshold_id(score: float, ai: float) -> str:
    return f"score_{int(round(float(score))):03d}_ai_{int(round(float(ai) * 100)):03d}"


def threshold_axes(timeframe: str) -> tuple[list[float], list[float], float, float]:
    live_score, live_ai = thresholds_for(timeframe)
    scores = sorted(set(float(value) for value in GRID_SCORES) | {float(live_score)})
    ai_values = sorted(set(float(value) for value in GRID_AI_PROBS) | {float(live_ai)})
    return scores, ai_values, float(live_score), float(live_ai)


def _write_candidate(tf_dir: Path, candidate: str, result: dict, common: dict,
                     score: float, ai: float, is_live_baseline: bool) -> dict:
    if result.get("decision_only") is not True:
        raise RuntimeError(f"{tf_dir.name}/{candidate}: Python trade resolution was not disabled.")
    signals = result.get("lean_events")
    if not isinstance(signals, list):
        raise RuntimeError(f"{tf_dir.name}/{candidate}: LEAN decision events are missing.")

    bar_times = set()
    with (tf_dir / "bars.csv").open(newline="", encoding="utf-8") as handle:
        import csv
        bar_times = {row["time"] for row in csv.DictReader(handle)}
    signal_times = [
        _utc_timestamp(event["signal_time"]).strftime("%Y-%m-%d %H:%M:%S.%f")
        for event in signals
    ]
    missing = sorted(set(signal_times) - bar_times)
    if missing:
        raise RuntimeError(f"{tf_dir.name}/{candidate}: decision timestamps do not match original OHLC: {missing[:5]}")
    if signal_times != sorted(signal_times) or len(signal_times) != len(set(signal_times)):
        raise RuntimeError(f"{tf_dir.name}/{candidate}: decisions are not unique and ordered.")

    candidate_dir = tf_dir / "thresholds" / candidate
    candidate_dir.mkdir(parents=True, exist_ok=True)
    metadata = {
        **common,
        "strategy": candidate,
        "strategy_label": f"Score >= {score:g}, AI >= {ai * 100:.0f}%",
        "score_threshold": float(score),
        "ai_threshold": float(ai),
        "is_live_baseline": bool(is_live_baseline),
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
        json.dumps(signals, ensure_ascii=False, separators=(",", ":"), allow_nan=False), encoding="utf-8"
    )
    return {"status": "ready", "metadata": metadata}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset", choices=tuple(ASSETS), required=True)
    parser.add_argument("--timeframes", default="5m,15m,1h")
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--output-dir", default="lean-threshold-test-input")
    args = parser.parse_args()

    if len(args.request_id) != 32 or any(ch not in "0123456789abcdef" for ch in args.request_id):
        raise SystemExit("Invalid request ID.")
    timeframes = [part.strip() for part in args.timeframes.split(",") if part.strip()]
    unknown = sorted(set(timeframes) - set(TIMEFRAME_BARS))
    if not timeframes or unknown or len(timeframes) != len(set(timeframes)):
        raise SystemExit(f"Unsupported or duplicate timeframes: {unknown or timeframes}")

    asset = ASSETS[args.asset]
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    backtest = GoldBacktest()
    cutoff = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    print(f"[threshold-test] exclusive UTC cutoff: {cutoff.isoformat()}", flush=True)

    summary_timeframes = []
    for tf in timeframes:
        requested = TIMEFRAME_BARS[tf]
        prepared = backtest._prepare(tf, requested, symbol=asset["symbol"], end_before=cutoff)
        raw = prepared["raw"]
        features = prepared["features"]
        if len(features) < 1200:
            raise RuntimeError(f"{args.asset} {tf}: insufficient usable candles ({len(features)}).")
        source = str(prepared.get("market_data_source") or "").strip()
        if not source or "synthetic" in source.lower():
            raise RuntimeError(f"{args.asset} {tf}: historical provider is not identified as real data.")

        tf_dir = output / tf
        tf_dir.mkdir(parents=True, exist_ok=True)
        bars_count, daily_counts = _write_bars(tf_dir, raw)
        first_feature = _utc_timestamp(features.index[0])
        last_feature = _utc_timestamp(features.index[-1])
        midpoint = first_feature + (last_feature - first_feature) / 2
        raw_start = _utc_timestamp(raw.index[0])
        raw_end = _utc_timestamp(raw.index[-1])
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

        # Retain the legacy score/probability grid and add the operational
        # pair for this timeframe so the live configuration is always a baseline.
        scores, ai_values, live_score, live_ai = threshold_axes(tf)
        predictions = backtest._walk_forward_predictions(
            tf, features, BACKTEST_RETRAIN_EVERY, asset["symbol"], "baseline"
        )

        threshold_results = {}
        threshold_order = []
        total = len(scores) * len(ai_values)
        index = 0
        for score in scores:
            for ai in ai_values:
                index += 1
                candidate = threshold_id(score, ai)
                threshold_order.append(candidate)
                is_baseline = abs(score - live_score) < 1e-9 and abs(ai - live_ai) < 1e-9
                print(
                    f"[threshold-test] {args.asset} {tf} {index}/{total}: "
                    f"{candidate} (live_baseline={is_baseline})",
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
                    include_lean_payload=True,
                    decision_only=True,
                    end_before=cutoff,
                )
                if result.get("error"):
                    threshold_results[candidate] = {
                        "status": "not_testable",
                        "error": str(result["error"]),
                        "score_threshold": score,
                        "ai_threshold": ai,
                        "is_live_baseline": is_baseline,
                        "model_fit_diagnostics": result.get("model_fit_diagnostics", {}),
                    }
                    continue
                threshold_results[candidate] = _write_candidate(
                    tf_dir, candidate, result, common, score, ai, is_baseline
                )

        summary_timeframes.append({
            "metadata": common,
            "active_thresholds": {"score": float(live_score), "ai": float(live_ai)},
            "scores": scores,
            "ai_probabilities": ai_values,
            "threshold_order": threshold_order,
            "thresholds": threshold_results,
        })

    if not summary_timeframes:
        raise SystemExit("No timeframe was prepared for threshold testing.")
    summary = {
        "report_type": "LEAN real historical threshold test",
        "market_data_type": "real_historical_ohlc",
        "synthetic_fixture": False,
        "request_id": args.request_id,
        "asset_key": args.asset,
        "engine": "QuantConnect LEAN open-source engine",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "timeframes": summary_timeframes,
        "experiment_design": (
            "Each timeframe uses one identical real OHLC snapshot and one set of chronological "
            "walk-forward baseline XGBoost predictions for every Score/AI threshold pair. Python "
            "exports decision-only signals; each candidate is replayed independently in LEAN, "
            "which alone resolves entries, fills, exits, and fees. Candidate selection uses the "
            "calibration half only; the later holdout is reported independently and never selects "
            "the threshold. The current live threshold pair is always included as a baseline."
        ),
    }
    (output / "prepared_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
