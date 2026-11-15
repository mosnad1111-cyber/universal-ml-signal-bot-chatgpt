"""Verify the complete strategy x Score/AI matrix from real LEAN executions."""
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

from scripts.prepare_lean_strategy_lab import threshold_id
from strategy_lab import STRATEGIES, STRATEGY_NAMES, split_completed_trades, summarize_trades


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _validate_execution(metadata: dict, report: dict, request_id: str,
                         asset_key: str, timeframe: str, candidate_id: str) -> None:
    source = str(metadata.get("market_data_source") or "").strip()
    if not source or "synthetic" in source.lower():
        raise SystemExit(f"{timeframe}/{candidate_id}: real market-data provider is missing.")
    if metadata.get("market_data_type") != "real_historical_ohlc":
        raise SystemExit(f"{timeframe}/{candidate_id}: input is not marked real historical OHLC.")
    if metadata.get("signals_aligned_to_ohlc") is not True or metadata.get("decision_only") is not True:
        raise SystemExit(f"{timeframe}/{candidate_id}: decision-only signal provenance was not verified.")
    if report.get("request_id") != request_id:
        raise SystemExit(f"{timeframe}/{candidate_id}: LEAN request ID mismatch.")
    if report.get("asset_key") != asset_key or report.get("timeframe") != timeframe:
        raise SystemExit(f"{timeframe}/{candidate_id}: LEAN asset/timeframe mismatch.")
    if metadata.get("strategy") != candidate_id or report.get("strategy") != candidate_id:
        raise SystemExit(f"{timeframe}/{candidate_id}: LEAN candidate identity mismatch.")
    try:
        expected_cash = float(metadata["lean_initial_cash_usd"])
        expected_max_price = float(metadata["max_ohlc_price"])
        cash_buffer = float(metadata["lean_cash_buffer_multiplier"])
        reported_cash = float(report["initial_cash_usd"])
        reported_max_price = float(report["max_ohlc_price"])
        reported_quantity = float(report["position_quantity_units"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SystemExit(
            f"{timeframe}/{candidate_id}: LEAN one-unit buying-power metadata is incomplete: {exc}"
        ) from exc
    if not all(math.isfinite(value) for value in (
        expected_cash, expected_max_price, cash_buffer,
        reported_cash, reported_max_price, reported_quantity,
    )):
        raise SystemExit(f"{timeframe}/{candidate_id}: LEAN buying-power metadata is not finite.")
    if expected_cash <= 0 or expected_max_price <= 0 or cash_buffer < 1.0 or reported_quantity != 1.0:
        raise SystemExit(f"{timeframe}/{candidate_id}: LEAN one-unit buying-power configuration is invalid.")
    if not (
        math.isclose(reported_cash, expected_cash, rel_tol=1e-9, abs_tol=1e-6)
        and math.isclose(reported_max_price, expected_max_price, rel_tol=1e-9, abs_tol=1e-6)
        and expected_cash >= expected_max_price * cash_buffer
    ):
        raise SystemExit(f"{timeframe}/{candidate_id}: LEAN buying power does not match prepared OHLC notional.")
    if report.get("market_data_source") != source:
        raise SystemExit(f"{timeframe}/{candidate_id}: LEAN market-data provenance mismatch.")
    if report.get("history_start_utc") != metadata.get("start"):
        raise SystemExit(f"{timeframe}/{candidate_id}: LEAN replay start differs from prepared OHLC.")
    if report.get("history_end_utc") != metadata.get("end"):
        raise SystemExit(f"{timeframe}/{candidate_id}: LEAN replay end differs from prepared OHLC.")
    if report.get("end_before_exclusive_utc") != metadata.get("end_before_exclusive_utc"):
        raise SystemExit(f"{timeframe}/{candidate_id}: LEAN and Python UTC cutoffs differ.")
    if int(report.get("excluded_current_day_bars", -1)) != int(metadata.get("end_window_excluded_bars", -2)):
        raise SystemExit(f"{timeframe}/{candidate_id}: excluded current-day bar counts differ.")
    if _utc(metadata["end"]).date() >= datetime.now(timezone.utc).date():
        raise SystemExit(f"{timeframe}/{candidate_id}: input contains the current UTC day.")
    if int(metadata.get("raw_bars", 0)) < 1200 or int(metadata.get("usable_bars", 0)) < 1200:
        raise SystemExit(f"{timeframe}/{candidate_id}: insufficient real historical candles.")

    expected_bars = int(metadata.get("bars_count", -1))
    received_bars = int(report.get("received_bars", -2))
    reader_parsed = int(report.get("reader_parsed_rows", -3))
    reader_unique = int(report.get("reader_unique_timestamps", -4))
    if int(report.get("bars", -5)) != expected_bars:
        raise SystemExit(f"{timeframe}/{candidate_id}: LEAN metadata candle count mismatch.")
    if int(report.get("reader_invalid_rows", -1)) != 0:
        raise SystemExit(f"{timeframe}/{candidate_id}: LEAN rejected CSV rows.")
    if int(report.get("reader_duplicate_timestamps", -1)) != 0:
        raise SystemExit(f"{timeframe}/{candidate_id}: LEAN encountered duplicate timestamps.")
    if reader_parsed != expected_bars or reader_unique != expected_bars or received_bars != expected_bars:
        raise SystemExit(
            f"{timeframe}/{candidate_id}: LEAN did not read and deliver every prepared OHLC bar "
            f"(expected={expected_bars}, parsed={reader_parsed}, unique={reader_unique}, "
            f"OnData={received_bars})."
        )

    expected_daily = metadata.get("daily_bar_counts", {})
    for key, actual_key in (
        ("reader_daily_counts", "CSV reader"),
        ("on_data_daily_counts", "OnData delivery"),
    ):
        actual = report.get(key, {})
        if expected_daily != actual:
            mismatches = {
                day: {"prepared": expected_daily.get(day, 0), actual_key: actual.get(day, 0)}
                for day in sorted(set(expected_daily) | set(actual))
                if expected_daily.get(day, 0) != actual.get(day, 0)
            }
            raise SystemExit(f"{timeframe}/{candidate_id}: LEAN per-day {actual_key} mismatch: {mismatches}")
    if report.get("order_errors"):
        raise SystemExit(f"{timeframe}/{candidate_id}: LEAN order execution errors: {report['order_errors']}")

    ledger = report.get("trades_detail")
    if not isinstance(ledger, list) or int(report.get("trades", -1)) != len(ledger):
        raise SystemExit(f"{timeframe}/{candidate_id}: LEAN trade ledger count mismatch.")
    try:
        gross = sum(float(trade["gross_r"]) for trade in ledger)
        fees = sum(float(trade["fee_r"]) for trade in ledger)
        net = sum(float(trade["net_r"]) for trade in ledger)
        report_gross = float(report["gross_r"])
        report_fees = float(report["fees_r"])
        report_net = float(report["net_r"])
        fees_usd = float(report.get("lean_total_fees_usd", 0.0))
        for trade in ledger:
            _utc(trade["signal_time"])
            _utc(trade["exit_time"])
            if str(trade.get("direction", "")).upper() not in ("BUY", "SELL"):
                raise SystemExit(
                    f"{timeframe}/{candidate_id}: LEAN trade ledger has a missing or invalid direction."
                )
    except (KeyError, TypeError, ValueError) as exc:
        raise SystemExit(f"{timeframe}/{candidate_id}: LEAN trade ledger is incomplete: {exc}") from exc
    values = (gross, fees, net, report_gross, report_fees, report_net, fees_usd)
    if not all(math.isfinite(value) for value in values) or fees < 0 or fees_usd < 0:
        raise SystemExit(f"{timeframe}/{candidate_id}: LEAN report has invalid return/fee values.")
    if not (
        math.isclose(gross, report_gross, rel_tol=1e-9, abs_tol=1e-8)
        and math.isclose(fees, report_fees, rel_tol=1e-9, abs_tol=1e-8)
        and math.isclose(net, report_net, rel_tol=1e-9, abs_tol=1e-8)
    ):
        raise SystemExit(f"{timeframe}/{candidate_id}: LEAN P/L totals do not reconcile with trade ledger.")
    wins = sum(float(trade["net_r"]) > 0 for trade in ledger)
    losses = sum(float(trade["net_r"]) < 0 for trade in ledger)
    if wins != int(report.get("wins", -1)) or losses != int(report.get("losses", -1)):
        raise SystemExit(f"{timeframe}/{candidate_id}: LEAN win/loss totals do not reconcile.")
    if int(report.get("signals", -1)) != int(metadata.get("signals_count", -2)):
        raise SystemExit(f"{timeframe}/{candidate_id}: LEAN signal count differs from prepared decisions.")


def _json_safe_metrics(metrics: dict) -> dict:
    safe = dict(metrics)
    for key, value in safe.items():
        if isinstance(value, float) and not math.isfinite(value):
            safe[key] = "Infinity" if value > 0 else ("-Infinity" if value < 0 else None)
    return safe


def _trade_sets(report: dict) -> tuple[list[dict], list[dict]]:
    gross, net = [], []
    ledger = report.get("trades_detail", [])
    if not isinstance(ledger, list):
        raise SystemExit("LEAN strategy-threshold trade ledger must be a list.")
    for item in ledger:
        direction = str(item.get("direction", "")).upper()
        if direction not in ("BUY", "SELL"):
            raise SystemExit("LEAN strategy-threshold trade ledger has an invalid direction.")
        gross.append({
            "time": item["signal_time"], "exit_time": item["exit_time"],
            "direction": direction, "r": float(item["gross_r"]),
        })
        net.append({
            "time": item["signal_time"], "exit_time": item["exit_time"],
            "direction": direction, "r": float(item["net_r"]),
        })
    return gross, net


def _candidate_metrics(metadata: dict, report: dict) -> dict:
    gross, net = _trade_sets(report)
    midpoint = metadata["midpoint"]
    calibration_gross, holdout_gross = split_completed_trades(gross, midpoint)
    calibration_net, holdout_net = split_completed_trades(net, midpoint)

    directional = {}
    for direction in ("BUY", "SELL"):
        side_gross = [item for item in gross if item["direction"] == direction]
        side_net = [item for item in net if item["direction"] == direction]
        side_cal_gross, side_hold_gross = split_completed_trades(side_gross, midpoint)
        side_cal_net, side_hold_net = split_completed_trades(side_net, midpoint)
        directional[direction] = {
            "all_gross": _json_safe_metrics(summarize_trades(side_gross, 0.0)),
            "calibration_gross": _json_safe_metrics(summarize_trades(side_cal_gross, 0.0)),
            "holdout_gross": _json_safe_metrics(summarize_trades(side_hold_gross, 0.0)),
            "all_net": _json_safe_metrics(summarize_trades(side_net, 0.0)),
            "calibration_net": _json_safe_metrics(summarize_trades(side_cal_net, 0.0)),
            "holdout_net": _json_safe_metrics(summarize_trades(side_hold_net, 0.0)),
            "holdout_cost_005R": _json_safe_metrics(summarize_trades(side_hold_gross, 0.05)),
            "holdout_cost_010R": _json_safe_metrics(summarize_trades(side_hold_gross, 0.10)),
        }

    report_fields = (
        "strategy", "trades", "signals", "bars", "received_bars",
        "reader_parsed_rows", "reader_unique_timestamps", "unfilled_setups",
        "unresolved_trades", "order_errors",
    )
    lean_validation = {key: report.get(key) for key in report_fields}
    return {
        "candidate_id": str(metadata["candidate_id"]),
        "strategy_type": str(metadata["strategy_type"]),
        "strategy_name": str(metadata["strategy_name"]),
        "threshold_id": str(metadata["threshold_id"]),
        "score_threshold": float(metadata["score_threshold"]),
        "ai_threshold": float(metadata["ai_threshold"]),
        "is_live_threshold_pair": bool(metadata.get("is_live_threshold_pair", False)),
        "is_live_baseline": bool(metadata.get("is_live_baseline", False)),
        "signals_count": int(metadata.get("signals_count", 0)),
        "setup_candidates": int(metadata.get("setup_candidates", metadata.get("signals_count", 0))),
        "unfilled_setups": int(report.get("unfilled_setups", 0)),
        "unresolved_trades": int(report.get("unresolved_trades", 0)),
        "directional": directional,
        "all_gross": _json_safe_metrics(summarize_trades(gross, 0.0)),
        "calibration_gross": _json_safe_metrics(summarize_trades(calibration_gross, 0.0)),
        "holdout_gross": _json_safe_metrics(summarize_trades(holdout_gross, 0.0)),
        "all_net": _json_safe_metrics(summarize_trades(net, 0.0)),
        "calibration_net": _json_safe_metrics(summarize_trades(calibration_net, 0.0)),
        "holdout_net": _json_safe_metrics(summarize_trades(holdout_net, 0.0)),
        "holdout_cost_005R": _json_safe_metrics(summarize_trades(holdout_gross, 0.05)),
        "holdout_cost_010R": _json_safe_metrics(summarize_trades(holdout_gross, 0.10)),
        "lean_report": lean_validation,
    }


def _pf_value(value) -> float:
    if value in ("Infinity", "inf", "∞"):
        return math.inf
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def _rank_key(candidate: dict, metric_key: str = "calibration_net") -> tuple[float, float, float]:
    calibration = candidate.get(metric_key, {})
    return (
        float(calibration.get("expectancy_after_cost_r", 0.0)),
        float(calibration.get("net_after_cost_r", 0.0)),
        _pf_value(calibration.get("profit_factor", 0.0)),
    )


def select_calibration_candidates(
    candidates: list[dict], min_trades: int = 30, metric_key: str = "calibration_net"
) -> tuple[dict | None, list[dict]]:
    """Only Calibration can rank candidates; Holdout metrics are never consulted."""
    eligible = []
    for item in candidates:
        calibration = item.get(metric_key, {})
        if (
            int(calibration.get("trades", 0)) >= int(min_trades)
            and float(calibration.get("net_after_cost_r", 0.0)) > 0
            and _pf_value(calibration.get("profit_factor", 0.0)) > 1.0
        ):
            eligible.append(item)
    eligible.sort(key=lambda item: _rank_key(item, metric_key), reverse=True)
    return (eligible[0] if eligible else None), eligible[:5]


def _compact_candidate(candidate: dict | None) -> dict | None:
    if not candidate:
        return None
    keys = (
        "candidate_id", "strategy_type", "strategy_name", "threshold_id",
        "score_threshold", "ai_threshold", "is_live_threshold_pair",
        "is_live_baseline", "signals_count", "calibration_gross",
        "calibration_net", "holdout_gross", "holdout_net",
        "holdout_cost_005R", "holdout_cost_010R", "directional",
        "unfilled_setups", "unresolved_trades",
    )
    return {key: candidate.get(key) for key in keys}


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
        or prepared.get("report_type") != "LEAN real historical strategy-threshold lab"
    ):
        raise SystemExit("Prepared combined strategy-threshold inputs do not match this request.")
    if prepared.get("market_data_type") != "real_historical_ohlc" or prepared.get("synthetic_fixture") is not False:
        raise SystemExit("Combined strategy/threshold lab requires real historical OHLC, not a synthetic fixture.")
    if prepared.get("strategy_order") != list(STRATEGIES):
        raise SystemExit("Prepared strategy order differs from supported research strategies.")

    timeframe_sections = []
    for timeframe, section in prepared.get("timeframes", {}).items():
        common = section.get("metadata", {})
        if common.get("timeframe") != timeframe or common.get("asset_key") != args.asset:
            raise SystemExit(f"{timeframe}: market-history metadata mismatch.")
        source = str(common.get("market_data_source") or "").strip()
        if not source or "synthetic" in source.lower() or common.get("market_data_type") != "real_historical_ohlc":
            raise SystemExit(f"{timeframe}: real historical data provenance is missing.")
        if int(common.get("raw_bars", 0)) < 1200 or int(common.get("usable_bars", 0)) < 1200:
            raise SystemExit(f"{timeframe}: insufficient historical candles.")
        if _utc(common["end"]).date() >= datetime.now(timezone.utc).date():
            raise SystemExit(f"{timeframe}: current UTC day was not excluded.")

        scores = [float(value) for value in section.get("scores", [])]
        ai_values = [float(value) for value in section.get("ai_probabilities", [])]
        threshold_order = section.get("threshold_order", [])
        if not scores or not ai_values or len(threshold_order) != len(scores) * len(ai_values):
            raise SystemExit(f"{timeframe}: threshold grid axes/order are incomplete.")
        if len(threshold_order) != len(set(threshold_order)):
            raise SystemExit(f"{timeframe}: duplicate threshold IDs.")
        expected_candidates = len(threshold_order)
        strategy_sections = section.get("strategies", {})
        if set(strategy_sections) != set(STRATEGIES):
            raise SystemExit(f"{timeframe}: strategy set is incomplete.")

        all_candidates = []
        result_by_strategy = {}
        executed_candidates = 0
        not_testable_candidates = 0
        live_baseline = None

        for strategy in STRATEGIES:
            strategy_section = strategy_sections[strategy]
            candidate_order = strategy_section.get("candidate_order", [])
            candidate_map = strategy_section.get("candidates", {})
            expected_order = [
                f"{strategy}__{item}" for item in threshold_order
            ]
            if candidate_order != expected_order or set(candidate_order) != set(candidate_map):
                raise SystemExit(f"{timeframe}/{strategy}: candidate order/list is incomplete.")
            results = {}
            for candidate_id in candidate_order:
                candidate_section = candidate_map[candidate_id]
                if candidate_section.get("status") == "not_testable":
                    if not candidate_section.get("error"):
                        raise SystemExit(f"{timeframe}/{candidate_id}: missing not-testable reason.")
                    not_testable_candidates += 1
                    results[candidate_id] = {
                        "status": "not_testable",
                        "candidate_id": candidate_id,
                        "error": str(candidate_section["error"]),
                        "threshold_id": candidate_section.get("threshold_id"),
                    }
                    continue
                if candidate_section.get("status") != "ready":
                    raise SystemExit(f"{timeframe}/{candidate_id}: invalid preparation status.")
                metadata_path = (
                    prepared_root / timeframe / "strategies" / strategy
                    / "candidates" / str(candidate_section["threshold_id"]) / "metadata.json"
                )
                report_path = results_root / timeframe / strategy / candidate_id / "lean_report.json"
                if not metadata_path.is_file() or not report_path.is_file():
                    raise SystemExit(f"{timeframe}/{candidate_id}: prepared metadata or LEAN report is missing.")
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                report = json.loads(report_path.read_text(encoding="utf-8"))
                expected_score, expected_ai = (
                    float(candidate_section["metadata"]["score_threshold"]),
                    float(candidate_section["metadata"]["ai_threshold"]),
                )
                if metadata.get("candidate_id") != candidate_id or metadata.get("strategy_type") != strategy:
                    raise SystemExit(f"{timeframe}/{candidate_id}: prepared strategy/candidate identity mismatch.")
                if (
                    not math.isclose(float(metadata.get("score_threshold", -1)), expected_score, abs_tol=1e-9)
                    or not math.isclose(float(metadata.get("ai_threshold", -1)), expected_ai, abs_tol=1e-9)
                ):
                    raise SystemExit(f"{timeframe}/{candidate_id}: prepared thresholds differ from candidate index.")
                _validate_execution(metadata, report, args.request_id, args.asset, timeframe, candidate_id)
                metrics = _candidate_metrics(metadata, report)
                results[candidate_id] = {"status": "executed", **metrics}
                all_candidates.append(metrics)
                executed_candidates += 1
                if metrics.get("is_live_baseline"):
                    if live_baseline is not None:
                        raise SystemExit(f"{timeframe}: more than one live baseline candidate was executed.")
                    live_baseline = metrics

            if strategy_section.get("status") == "not_testable" and not all(
                item.get("status") == "not_testable" for item in results.values()
            ):
                raise SystemExit(f"{timeframe}/{strategy}: strategy status conflicts with candidate outcomes.")
            strategy_selected, strategy_top = select_calibration_candidates(all_candidates_for_strategy := [
                item for item in all_candidates if item.get("strategy_type") == strategy
            ])
            direction_selected = {}
            for direction in ("BUY", "SELL"):
                # Directional ranking uses an explicit side-metric projection to
                # keep the same eligibility gate and never reads Holdout.
                side_rows = []
                for item in all_candidates_for_strategy:
                    side = item.get("directional", {}).get(direction, {})
                    row = {"candidate": item, "calibration_net": side.get("calibration_net", {})}
                    side_rows.append(row)
                side_rows.sort(
                    key=lambda row: _rank_key(
                        {"calibration_net": row["calibration_net"]}, "calibration_net"
                    ),
                    reverse=True,
                )
                eligible = [
                    row for row in side_rows
                    if int(row["calibration_net"].get("trades", 0)) >= 30
                    and float(row["calibration_net"].get("net_after_cost_r", 0.0)) > 0
                    and _pf_value(row["calibration_net"].get("profit_factor", 0.0)) > 1.0
                ]
                direction_selected[direction] = _compact_candidate(
                    eligible[0]["candidate"] if eligible else None
                )
            result_by_strategy[strategy] = {
                "status": "executed" if any(
                    item.get("status") == "executed" for item in results.values()
                ) else "not_testable",
                "error": strategy_section.get("error"),
                "candidate_order": candidate_order,
                "candidate_count": expected_candidates,
                "executed_candidates": sum(item.get("status") == "executed" for item in results.values()),
                "not_testable_candidates": sum(item.get("status") == "not_testable" for item in results.values()),
                "calibration_selected": _compact_candidate(strategy_selected),
                "top_calibration_candidates": [_compact_candidate(item) for item in strategy_top],
                "directional_selected": direction_selected,
                "candidates": results,
                "model_fit_diagnostics": strategy_section.get("model_fit_diagnostics", {}),
            }

        calibration_selected, top_candidates = select_calibration_candidates(all_candidates)
        directional_best = {}
        for direction in ("BUY", "SELL"):
            eligible = []
            for candidate in all_candidates:
                side = candidate.get("directional", {}).get(direction, {})
                cal = side.get("calibration_net", {})
                if (
                    int(cal.get("trades", 0)) >= 30
                    and float(cal.get("net_after_cost_r", 0.0)) > 0
                    and _pf_value(cal.get("profit_factor", 0.0)) > 1.0
                ):
                    eligible.append((candidate, cal))
            eligible.sort(
                key=lambda pair: (
                    float(pair[1].get("expectancy_after_cost_r", 0.0)),
                    float(pair[1].get("net_after_cost_r", 0.0)),
                    _pf_value(pair[1].get("profit_factor", 0.0)),
                ),
                reverse=True,
            )
            directional_best[direction] = _compact_candidate(eligible[0][0] if eligible else None)

        if executed_candidates == 0:
            raise SystemExit(f"{timeframe}: no combined strategy/threshold candidate was executed by LEAN.")
        timeframe_sections.append({
            "metadata": common,
            "scores": scores,
            "ai_probabilities": ai_values,
            "threshold_order": threshold_order,
            "candidate_count_per_strategy": expected_candidates,
            "expected_candidates": expected_candidates * len(STRATEGIES),
            "executed_candidates": executed_candidates,
            "not_testable_candidates": not_testable_candidates,
            "live_baseline": _compact_candidate(live_baseline),
            "calibration_selected": _compact_candidate(calibration_selected),
            "top_calibration_candidates": [_compact_candidate(item) for item in top_candidates],
            "directional_best": directional_best,
            "strategy_best": {
                strategy: result_by_strategy[strategy].get("calibration_selected")
                for strategy in STRATEGIES
            },
            "results": result_by_strategy,
        })

    if not timeframe_sections:
        raise SystemExit("No verified real-history combined strategy/threshold results were generated.")

    summary = {
        "report_type": "LEAN real historical strategy-threshold lab",
        "market_data_type": "real_historical_ohlc",
        "synthetic_fixture": False,
        "request_id": args.request_id,
        "asset_key": args.asset,
        "engine": "QuantConnect LEAN open-source engine",
        "strategy_order": list(STRATEGIES),
        "strategy_names": STRATEGY_NAMES,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "timeframes": timeframe_sections,
        "experiment_design": (
            "One identical real historical OHLC snapshot is used within each timeframe. Each strategy "
            "has its own single chronological Walk-Forward prediction stream; the full Score/AI grid is "
            "applied to that stream without re-training for each threshold pair. Python exports decision "
            "events only. Every strategy/threshold candidate is independently executed by QuantConnect "
            "LEAN, which alone resolves fills, exits, and fees. Combined and BUY/SELL candidate selection "
            "uses Calibration only; Holdout is independent and never participates in candidate ranking. "
            "The live baseline strategy and operational Score/AI pair are included."
        ),
    }
    (output_root / "lean-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
