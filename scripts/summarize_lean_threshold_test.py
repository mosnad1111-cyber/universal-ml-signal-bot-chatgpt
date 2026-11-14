"""Verify LEAN threshold replays and rank candidates on calibration only."""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.summarize_lean_strategy_lab import _json_safe_metrics, _validate_execution
from strategy_lab import summarize_trades


def _trade_sets(report: dict) -> tuple[list[dict], list[dict]]:
    gross, net = [], []
    ledger = report.get("trades_detail", [])
    if not isinstance(ledger, list):
        raise SystemExit("LEAN threshold trade ledger must be a list.")
    for item in ledger:
        direction = str(item.get("direction", "")).upper()
        if direction not in ("BUY", "SELL"):
            raise SystemExit("LEAN threshold trade ledger has a missing or invalid direction.")
        gross.append({
            "time": item["signal_time"],
            "exit_time": item["exit_time"],
            "direction": direction,
            "r": float(item["gross_r"]),
        })
        net.append({
            "time": item["signal_time"],
            "exit_time": item["exit_time"],
            "direction": direction,
            "r": float(item["net_r"]),
        })
    return gross, net


def _candidate_metrics(metadata: dict, report: dict) -> dict:
    gross, net = _trade_sets(report)
    midpoint = metadata["midpoint"]

    def split(items):
        # Reuse strategy_lab's strict signal+exit-time split rule.
        from strategy_lab import split_completed_trades
        return split_completed_trades(items, midpoint)

    calibration_gross, holdout_gross = split(gross)
    calibration_net, holdout_net = split(net)

    # Directional slices use the exact same LEAN trade ledger and the same
    # timestamp split as the combined result; no signal-side Python scoring.
    directional = {}
    for direction in ("BUY", "SELL"):
        side_gross = [item for item in gross if item["direction"] == direction]
        side_net = [item for item in net if item["direction"] == direction]
        side_cal_gross, side_hold_gross = split(side_gross)
        side_cal_net, side_hold_net = split(side_net)
        directional[direction] = {
            "all_gross": _json_safe_metrics(summarize_trades(side_gross, 0.0)),
            "calibration_gross": _json_safe_metrics(summarize_trades(side_cal_gross, 0.0)),
            "holdout_gross": _json_safe_metrics(summarize_trades(side_hold_gross, 0.0)),
            "all_net": _json_safe_metrics(summarize_trades(side_net, 0.0)),
            "calibration_net": _json_safe_metrics(summarize_trades(side_cal_net, 0.0)),
            "holdout_net": _json_safe_metrics(summarize_trades(side_hold_net, 0.0)),
            "holdout_cost_010R": _json_safe_metrics(summarize_trades(side_hold_gross, 0.10)),
        }

    return {
        "directional": directional,
        "all_gross": _json_safe_metrics(summarize_trades(gross, 0.0)),
        "calibration_gross": _json_safe_metrics(summarize_trades(calibration_gross, 0.0)),
        "holdout_gross": _json_safe_metrics(summarize_trades(holdout_gross, 0.0)),
        "all_net": _json_safe_metrics(summarize_trades(net, 0.0)),
        "calibration_net": _json_safe_metrics(summarize_trades(calibration_net, 0.0)),
        "holdout_net": _json_safe_metrics(summarize_trades(holdout_net, 0.0)),
        "holdout_cost_010R": _json_safe_metrics(summarize_trades(holdout_gross, 0.10)),
        "signals_count": int(metadata.get("signals_count", 0)),
        "setup_candidates": int(metadata.get("setup_candidates", metadata.get("signals_count", 0))),
        "unfilled_setups": int(report.get("unfilled_setups", 0)),
        "unresolved_trades": int(report.get("unresolved_trades", 0)),
        "is_live_baseline": bool(metadata.get("is_live_baseline", False)),
        "score_threshold": float(metadata["score_threshold"]),
        "ai_threshold": float(metadata["ai_threshold"]),
        "lean_report": report,
    }


def _rank_key(candidate: dict) -> tuple[float, float, float]:
    calibration = candidate.get("calibration_net", {})
    return (
        float(calibration.get("expectancy_after_cost_r", 0.0)),
        float(calibration.get("net_after_cost_r", 0.0)),
        float(calibration.get("profit_factor", 0.0))
        if isinstance(calibration.get("profit_factor"), (int, float))
        else (math.inf if calibration.get("profit_factor") == "Infinity" else 0.0),
    )


def select_calibration_candidates(executed: list[dict], min_trades: int = 30) -> tuple[dict | None, list[dict]]:
    """Select only from calibration metrics; holdout fields are deliberately ignored."""
    eligible = []
    for item in executed:
        calibration = item.get("calibration_net", {})
        pf = calibration.get("profit_factor", 0.0)
        pf_value = (
            math.inf if pf == "Infinity"
            else (float(pf) if isinstance(pf, (int, float)) else 0.0)
        )
        if (
            int(calibration.get("trades", 0)) >= int(min_trades)
            and float(calibration.get("net_after_cost_r", 0.0)) > 0
            and pf_value > 1.0
        ):
            eligible.append(item)
    eligible.sort(key=_rank_key, reverse=True)
    return (eligible[0] if eligible else None), eligible[:5]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-dir", required=True)
    parser.add_argument("--results-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--asset", required=True, choices=("GOLD", "BTC"))
    args = parser.parse_args()

    prepared_root = Path(args.prepared_dir)
    results_root = Path(args.results_dir)
    output_root = Path(args.output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    prepared = json.loads((prepared_root / "prepared_summary.json").read_text(encoding="utf-8"))
    if (
        prepared.get("request_id") != args.request_id
        or prepared.get("asset_key") != args.asset
        or prepared.get("report_type") != "LEAN real historical threshold test"
    ):
        raise SystemExit("Prepared threshold-test inputs do not match this request.")
    if prepared.get("market_data_type") != "real_historical_ohlc" or prepared.get("synthetic_fixture") is not False:
        raise SystemExit("Threshold test must use verified real historical OHLC, not a synthetic fixture.")

    timeframe_sections = []
    for section in prepared.get("timeframes", []):
        common = section.get("metadata", {})
        timeframe = str(common.get("timeframe", ""))
        if not timeframe or common.get("asset_key") != args.asset:
            raise SystemExit("Threshold test market-history metadata mismatch.")
        source = str(common.get("market_data_source") or "").strip()
        if not source or "synthetic" in source.lower() or common.get("market_data_type") != "real_historical_ohlc":
            raise SystemExit(f"{timeframe}: real historical data provenance is missing.")
        if int(common.get("raw_bars", 0)) < 1200 or int(common.get("usable_bars", 0)) < 1200:
            raise SystemExit(f"{timeframe}: insufficient historical OHLC/feature bars.")
        if datetime.fromisoformat(str(common["end"]).replace("Z", "+00:00")).date() >= datetime.now(timezone.utc).date():
            raise SystemExit(f"{timeframe}: prepared history includes the current UTC day.")

        candidates = section.get("thresholds", {})
        order = section.get("threshold_order", [])
        if not order or set(order) != set(candidates) or len(order) != len(set(order)):
            raise SystemExit(f"{timeframe}: threshold candidate list is incomplete or duplicated.")

        results = []
        for candidate_id in order:
            candidate = candidates[candidate_id]
            if candidate.get("status") == "not_testable":
                results.append({
                    "threshold_id": candidate_id,
                    "status": "not_testable",
                    "error": str(candidate.get("error", "No valid walk-forward predictions.")),
                    "score_threshold": float(candidate.get("score_threshold", 0.0)),
                    "ai_threshold": float(candidate.get("ai_threshold", 0.0)),
                    "is_live_baseline": bool(candidate.get("is_live_baseline", False)),
                })
                continue
            if candidate.get("status") != "ready":
                raise SystemExit(f"{timeframe}/{candidate_id}: invalid preparation status.")
            metadata = candidate.get("metadata", {})
            if metadata.get("strategy") != candidate_id or metadata.get("is_live_baseline") not in (True, False):
                raise SystemExit(f"{timeframe}/{candidate_id}: threshold metadata identity mismatch.")
            report_path = results_root / timeframe / candidate_id / "lean_report.json"
            if not report_path.is_file():
                raise SystemExit(f"{timeframe}/{candidate_id}: LEAN output is missing.")
            report = json.loads(report_path.read_text(encoding="utf-8"))
            _validate_execution(metadata, report, args.request_id, args.asset, timeframe, candidate_id)
            metrics = _candidate_metrics(metadata, report)
            results.append({
                "threshold_id": candidate_id,
                "status": "executed",
                "metadata": metadata,
                **metrics,
            })

        executed = [item for item in results if item["status"] == "executed"]
        baseline = next((item for item in executed if item.get("is_live_baseline")), None)
        # Do not use the holdout to choose parameters. Require an adequate
        # calibration sample, positive net expectancy, and PF > 1.
        selected, top_candidates = select_calibration_candidates(executed)

        timeframe_sections.append({
            "metadata": common,
            "active_thresholds": section.get("active_thresholds", {}),
            "scores": section.get("scores", []),
            "ai_probabilities": section.get("ai_probabilities", []),
            "threshold_results": results,
            "executed_candidates": len(executed),
            "not_testable_candidates": len(results) - len(executed),
            "live_baseline_threshold_id": baseline.get("threshold_id") if baseline else None,
            "live_baseline": baseline,
            "calibration_selected_threshold_id": selected.get("threshold_id") if selected else None,
            "calibration_selected": selected,
            "top_calibration_candidates": top_candidates,
            "selection_rule": (
                "Calibration-only: at least 30 completed calibration trades, positive net expectancy "
                "after LEAN's 0.05R round-trip fee assumption, and profit factor > 1.0. "
                "Holdout metrics are not used for ranking."
            ),
        })

    if not timeframe_sections:
        raise SystemExit("No verified real-history LEAN threshold results were generated.")
    summary = {
        "report_type": "LEAN real historical threshold test",
        "market_data_type": "real_historical_ohlc",
        "synthetic_fixture": False,
        "request_id": args.request_id,
        "asset_key": args.asset,
        "engine": "QuantConnect LEAN open-source engine",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "timeframes": timeframe_sections,
        "experiment_design": prepared.get("experiment_design", ""),
        "warning": (
            "Thresholds are not auto-applied. Compare the calibration-selected candidate with the "
            "untouched holdout result, then validate on a fresh future period before changing live settings. "
            "The 0.05R LEAN fee assumption is not a broker quote."
        ),
    }
    (output_root / "lean-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
