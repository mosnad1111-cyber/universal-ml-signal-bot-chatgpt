"""Validate real-market LEAN executions and build the strategy-lab report."""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
import sys

# Direct execution (python scripts/...) puts only the scripts directory on
# sys.path. Add the repository root so sibling top-level modules are importable
# in GitHub Actions and other callers regardless of their working directory.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from strategy_lab import STRATEGIES, STRATEGY_NAMES, split_completed_trades, summarize_trades


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _validate_execution(metadata: dict, report: dict, request_id: str,
                         asset_key: str, timeframe: str, strategy: str) -> None:
    source = str(metadata.get("market_data_source") or "").strip()
    if not source or "synthetic" in source.lower():
        raise SystemExit(f"{timeframe}/{strategy}: real market-data provider is missing.")
    if metadata.get("market_data_type") != "real_historical_ohlc":
        raise SystemExit(f"{timeframe}/{strategy}: input is not marked real historical OHLC.")
    if metadata.get("signals_aligned_to_ohlc") is not True or metadata.get("decision_only") is not True:
        raise SystemExit(f"{timeframe}/{strategy}: decision-only signal provenance was not verified.")
    if report.get("request_id") != request_id:
        raise SystemExit(f"{timeframe}/{strategy}: LEAN request ID mismatch.")
    if report.get("asset_key") != asset_key or report.get("timeframe") != timeframe:
        raise SystemExit(f"{timeframe}/{strategy}: LEAN asset/timeframe mismatch.")
    if report.get("strategy") != strategy:
        raise SystemExit(f"{timeframe}/{strategy}: LEAN executed a different strategy.")
    try:
        expected_cash = float(metadata["lean_initial_cash_usd"])
        expected_max_price = float(metadata["max_ohlc_price"])
        cash_buffer = float(metadata["lean_cash_buffer_multiplier"])
        reported_cash = float(report["initial_cash_usd"])
        reported_max_price = float(report["max_ohlc_price"])
        reported_quantity = float(report["position_quantity_units"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SystemExit(f"{timeframe}/{strategy}: LEAN one-unit buying-power metadata is incomplete: {exc}") from exc
    if not all(math.isfinite(value) for value in (
        expected_cash, expected_max_price, cash_buffer,
        reported_cash, reported_max_price, reported_quantity,
    )):
        raise SystemExit(f"{timeframe}/{strategy}: LEAN buying-power metadata is not finite.")
    if expected_cash <= 0 or expected_max_price <= 0 or cash_buffer < 1.0 or reported_quantity != 1.0:
        raise SystemExit(f"{timeframe}/{strategy}: LEAN one-unit buying-power configuration is invalid.")
    if not (
        math.isclose(reported_cash, expected_cash, rel_tol=1e-9, abs_tol=1e-6)
        and math.isclose(reported_max_price, expected_max_price, rel_tol=1e-9, abs_tol=1e-6)
        and expected_cash >= expected_max_price * cash_buffer
    ):
        raise SystemExit(f"{timeframe}/{strategy}: LEAN buying power does not match prepared OHLC notional.")
    if report.get("market_data_source") != source:
        raise SystemExit(f"{timeframe}/{strategy}: LEAN market-data provenance mismatch.")
    if report.get("history_start_utc") != metadata.get("start"):
        raise SystemExit(f"{timeframe}/{strategy}: LEAN replay start differs from prepared OHLC.")
    if report.get("history_end_utc") != metadata.get("end"):
        raise SystemExit(f"{timeframe}/{strategy}: LEAN replay end differs from prepared OHLC.")
    if report.get("end_before_exclusive_utc") != metadata.get("end_before_exclusive_utc"):
        raise SystemExit(f"{timeframe}/{strategy}: LEAN and Python UTC cutoffs differ.")
    if int(report.get("excluded_current_day_bars", -1)) != int(metadata.get("end_window_excluded_bars", -2)):
        raise SystemExit(f"{timeframe}/{strategy}: excluded current-day bar counts differ.")
    if _utc(metadata["end"]).date() >= datetime.now(timezone.utc).date():
        raise SystemExit(f"{timeframe}/{strategy}: input contains the current UTC day.")
    if int(metadata.get("raw_bars", 0)) < 1200 or int(metadata.get("usable_bars", 0)) < 1200:
        raise SystemExit(f"{timeframe}/{strategy}: insufficient real historical candles.")
    if int(report.get("signals", -1)) != int(metadata.get("signals_count", -2)):
        raise SystemExit(f"{timeframe}/{strategy}: LEAN signal count differs from prepared decisions.")

    expected_bars = int(metadata.get("bars_count", -1))
    received_bars = int(report.get("received_bars", -2))
    reader_parsed = int(report.get("reader_parsed_rows", -3))
    reader_unique = int(report.get("reader_unique_timestamps", -4))
    if int(report.get("bars", -5)) != expected_bars:
        raise SystemExit(f"{timeframe}/{strategy}: LEAN metadata candle count mismatch.")
    if int(report.get("reader_invalid_rows", -1)) != 0:
        raise SystemExit(f"{timeframe}/{strategy}: LEAN rejected CSV rows.")
    if int(report.get("reader_duplicate_timestamps", -1)) != 0:
        raise SystemExit(f"{timeframe}/{strategy}: LEAN encountered duplicate timestamps.")
    if reader_parsed != expected_bars or reader_unique != expected_bars or received_bars != expected_bars:
        raise SystemExit(
            f"{timeframe}/{strategy}: LEAN did not read and deliver every prepared OHLC bar "
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
            raise SystemExit(f"{timeframe}/{strategy}: LEAN per-day {actual_key} mismatch: {mismatches}")
    if report.get("order_errors"):
        raise SystemExit(f"{timeframe}/{strategy}: LEAN order execution errors: {report['order_errors']}")

    ledger = report.get("trades_detail")
    if not isinstance(ledger, list) or int(report.get("trades", -1)) != len(ledger):
        raise SystemExit(f"{timeframe}/{strategy}: LEAN trade ledger count mismatch.")
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
    except (KeyError, TypeError, ValueError) as exc:
        raise SystemExit(f"{timeframe}/{strategy}: LEAN trade ledger is incomplete: {exc}") from exc
    values = (gross, fees, net, report_gross, report_fees, report_net, fees_usd)
    if not all(math.isfinite(value) for value in values) or fees < 0 or fees_usd < 0:
        raise SystemExit(f"{timeframe}/{strategy}: LEAN report has invalid return/fee values.")
    if not (
        math.isclose(gross, report_gross, rel_tol=1e-9, abs_tol=1e-8)
        and math.isclose(fees, report_fees, rel_tol=1e-9, abs_tol=1e-8)
        and math.isclose(net, report_net, rel_tol=1e-9, abs_tol=1e-8)
    ):
        raise SystemExit(f"{timeframe}/{strategy}: LEAN P/L totals do not reconcile with trade ledger.")
    wins = sum(float(trade["net_r"]) > 0 for trade in ledger)
    losses = sum(float(trade["net_r"]) < 0 for trade in ledger)
    if wins != int(report.get("wins", -1)) or losses != int(report.get("losses", -1)):
        raise SystemExit(f"{timeframe}/{strategy}: LEAN win/loss totals do not reconcile.")


def _json_safe_metrics(metrics: dict) -> dict:
    """Keep JSON standards-compliant when profit factor is mathematically infinite."""
    safe = dict(metrics)
    for key, value in safe.items():
        if isinstance(value, float) and not math.isfinite(value):
            safe[key] = "Infinity" if value > 0 else ("-Infinity" if value < 0 else None)
    return safe


def _strategy_metrics(metadata: dict, report: dict) -> dict:
    trades = [
        {
            "time": item["signal_time"],
            "exit_time": item["exit_time"],
            "r": float(item["gross_r"]),
        }
        for item in report.get("trades_detail", [])
    ]
    midpoint = _utc(metadata["midpoint"])
    calibration, holdout = split_completed_trades(trades, midpoint)
    return {
        "all": _json_safe_metrics(summarize_trades(trades, 0.0)),
        "calibration": _json_safe_metrics(summarize_trades(calibration, 0.0)),
        "holdout": _json_safe_metrics(summarize_trades(holdout, 0.0)),
        "holdout_cost_005R": _json_safe_metrics(summarize_trades(holdout, 0.05)),
        "holdout_cost_010R": _json_safe_metrics(summarize_trades(holdout, 0.10)),
        "setup_candidates": int(metadata.get("setup_candidates", metadata.get("signals_count", 0))),
        "signals_count": int(metadata.get("signals_count", 0)),
        "model_fit_diagnostics": metadata.get("model_fit_diagnostics", {}),
        "rejection_counts": metadata.get("rejection_counts", {}),
        "direction_rejections": metadata.get("direction_rejections", {}),
        "unfilled_setups": int(report.get("unfilled_setups", 0)),
        "unresolved_trades": int(report.get("unresolved_trades", 0)),
        "lean_report": report,
    }


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
    if prepared.get("request_id") != args.request_id or prepared.get("asset_key") != args.asset:
        raise SystemExit("Prepared strategy-lab inputs do not match this request.")
    if prepared.get("strategy_order") != list(STRATEGIES):
        raise SystemExit("Prepared strategy list differs from the supported lab design.")

    timeframes = []
    for timeframe, section in prepared.get("timeframes", {}).items():
        common = section.get("metadata", {})
        if common.get("timeframe") != timeframe or common.get("asset_key") != args.asset:
            raise SystemExit(f"{timeframe}: common market history metadata mismatch.")
        if not common.get("market_data_source") or "synthetic" in str(common["market_data_source"]).lower():
            raise SystemExit(f"{timeframe}: historical market-data source is not verified.")
        if common.get("market_data_type") != "real_historical_ohlc":
            raise SystemExit(f"{timeframe}: history is not identified as real OHLC.")
        if int(common.get("bars_count", 0)) < 1200 or int(common.get("usable_bars", 0)) < 1200:
            raise SystemExit(f"{timeframe}: insufficient historical candles.")
        if _utc(common["end"]).date() >= datetime.now(timezone.utc).date():
            raise SystemExit(f"{timeframe}: current UTC day was not excluded.")

        actual_strategies = section.get("strategies", {})
        if set(actual_strategies) != set(STRATEGIES):
            raise SystemExit(f"{timeframe}: strategy list is incomplete.")
        results = {}
        for strategy in STRATEGIES:
            status = actual_strategies[strategy].get("status")
            if status == "not_testable":
                results[strategy] = {
                    "status": "not_testable",
                    "error": str(actual_strategies[strategy].get("error", "No valid walk-forward predictions.")),
                    "model_fit_diagnostics": actual_strategies[strategy].get("model_fit_diagnostics", {}),
                }
                continue
            if status != "ready":
                raise SystemExit(f"{timeframe}/{strategy}: invalid preparation status.")
            metadata = actual_strategies[strategy].get("metadata", {})
            if metadata.get("strategy") != strategy or metadata.get("decision_only") is not True:
                raise SystemExit(f"{timeframe}/{strategy}: decision stream metadata mismatch.")
            if metadata.get("signals_aligned_to_ohlc") is not True:
                raise SystemExit(f"{timeframe}/{strategy}: signal-to-candle alignment not verified.")
            report_path = results_root / timeframe / strategy / "lean_report.json"
            if not report_path.is_file():
                raise SystemExit(f"{timeframe}/{strategy}: LEAN report is missing.")
            report = json.loads(report_path.read_text(encoding="utf-8"))
            _validate_execution(metadata, report, args.request_id, args.asset, timeframe, strategy)
            results[strategy] = {
                "status": "executed",
                "metadata": metadata,
                **_strategy_metrics(metadata, report),
            }
            out_dir = output_root / timeframe / strategy
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "metadata.json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (out_dir / "lean_report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        timeframes.append({"metadata": common, "results": results})

    if not timeframes:
        raise SystemExit("No verified real-history LEAN strategy results were generated.")
    summary = {
        "report_type": "LEAN real historical strategy research lab",
        "market_data_type": "real_historical_ohlc",
        "synthetic_fixture": False,
        "request_id": args.request_id,
        "asset_key": args.asset,
        "engine": "QuantConnect LEAN open-source engine",
        "strategy_order": list(STRATEGIES),
        "strategy_names": STRATEGY_NAMES,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "timeframes": timeframes,
        "experiment_design": (
            "Strategy-specific chronological XGBoost walk-forward decisions are generated in Python. "
            "Python does not resolve or score trades. Each ready strategy's decisions and the same raw "
            "historical OHLC stream are replayed independently by LEAN. Calibration and holdout metrics "
            "are computed only from LEAN's executed trade ledger. Holdout cost sensitivities are explicit "
            "0.05R and 0.10R per closed trade assumptions, not broker-specific spreads or commissions."
        ),
    }
    (output_root / "lean-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
