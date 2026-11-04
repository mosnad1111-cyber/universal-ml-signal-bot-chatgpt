"""Validate and combine real-market LEAN reports for Telegram delivery."""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path


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
        raise SystemExit("Prepared market snapshot does not match this request.")

    timeframes = []
    for tf, item in prepared.get("timeframes", {}).items():
        metadata = item.get("metadata", {})
        source = str(metadata.get("market_data_source") or "").strip()
        if not source or "synthetic" in source.lower():
            raise SystemExit(f"{tf}: source is not identified as a real market provider.")
        if int(metadata.get("raw_bars", 0)) < 1200 or int(metadata.get("usable_bars", 0)) < 1200:
            raise SystemExit(f"{tf}: insufficient real historical data.")
        report_path = results_root / tf / "lean_report.json"
        if not report_path.is_file():
            raise SystemExit(f"{tf}: LEAN report is missing.")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if report.get("request_id") != args.request_id:
            raise SystemExit(f"{tf}: LEAN report request ID mismatch.")
        if report.get("asset_key") != args.asset or report.get("timeframe") != tf:
            raise SystemExit(f"{tf}: LEAN report instrument/timeframe mismatch.")
        if report.get("market_data_source") != source:
            raise SystemExit(f"{tf}: LEAN report market-data provenance does not match prepared history.")
        if int(report.get("signals", -1)) != int(metadata.get("signals_count", -2)):
            raise SystemExit(f"{tf}: LEAN signal count differs from the prepared walk-forward signals.")
        expected_bars = int(report.get("bars", -1))
        received_bars = int(report.get("received_bars", -2))
        reader_parsed = int(report.get("reader_parsed_rows", -1))
        reader_unique = int(report.get("reader_unique_timestamps", -1))
        if int(report.get("reader_invalid_rows", -1)) != 0:
            raise SystemExit(f"{tf}: LEAN rejected {report.get('reader_invalid_rows')} CSV rows.")
        if int(report.get("reader_duplicate_timestamps", -1)) != 0:
            raise SystemExit(f"{tf}: LEAN received duplicate OHLC timestamps.")
        if reader_parsed != expected_bars or reader_unique != expected_bars:
            raise SystemExit(
                f"{tf}: LEAN CSV reader parsed {reader_parsed} rows / {reader_unique} "
                f"unique timestamps for {expected_bars} prepared bars "
                f"(invalid_rows={report.get('reader_invalid_rows')}, "
                f"duplicate_rows={report.get('reader_duplicate_timestamps')}, "
                f"source_requests={report.get('reader_source_requests')})."
            )
        expected_daily = metadata.get("daily_bar_counts", {})
        received_daily = report.get("reader_daily_counts", {})
        if expected_daily != received_daily:
            mismatches = {
                day: {"prepared": expected_daily.get(day, 0), "reader": received_daily.get(day, 0)}
                for day in sorted(set(expected_daily) | set(received_daily))
                if expected_daily.get(day, 0) != received_daily.get(day, 0)
            }
            raise SystemExit(f"{tf}: LEAN per-day OHLC ingestion mismatch: {mismatches}")
        if expected_bars != received_bars:
            raise SystemExit(
                f"{tf}: LEAN emitted {received_bars}/{expected_bars} prepared bars "
                f"to OnData after reading {reader_unique} unique CSV timestamps."
            )
        if expected_bars != int(metadata.get("bars_count", -1)):
            raise SystemExit(f"{tf}: LEAN input candle count differs from prepared history.")
        if report.get("order_errors"):
            raise SystemExit(f"{tf}: LEAN order execution errors: {report['order_errors']}")
        trade_details = report.get("trades_detail")
        if not isinstance(trade_details, list) or int(report.get("trades", -1)) != len(trade_details):
            raise SystemExit(f"{tf}: LEAN trade count does not match its executed trade ledger.")
        try:
            gross_sum = sum(float(trade["gross_r"]) for trade in trade_details)
            fees_sum = sum(float(trade["fee_r"]) for trade in trade_details)
            net_sum = sum(float(trade["net_r"]) for trade in trade_details)
            gross_report = float(report["gross_r"])
            fees_report = float(report["fees_r"])
            net_report = float(report["net_r"])
            total_fees_usd = float(report.get("lean_total_fees_usd", 0.0))
        except (KeyError, TypeError, ValueError) as exc:
            raise SystemExit(f"{tf}: LEAN trade ledger or summary metrics are incomplete: {exc}")
        metrics = (gross_sum, fees_sum, net_sum, gross_report, fees_report, net_report, total_fees_usd)
        if not all(math.isfinite(value) for value in metrics) or total_fees_usd < 0:
            raise SystemExit(f"{tf}: LEAN report contains non-finite or invalid fee/return values.")
        if not (
            math.isclose(gross_sum, gross_report, rel_tol=1e-9, abs_tol=1e-8)
            and math.isclose(fees_sum, fees_report, rel_tol=1e-9, abs_tol=1e-8)
            and math.isclose(net_sum, net_report, rel_tol=1e-9, abs_tol=1e-8)
        ):
            raise SystemExit(f"{tf}: LEAN return/fee totals do not reconcile with the executed trade ledger.")
        wins = sum(float(trade["net_r"]) > 0 for trade in trade_details)
        losses = sum(float(trade["net_r"]) < 0 for trade in trade_details)
        if wins != int(report.get("wins", -1)) or losses != int(report.get("losses", -1)):
            raise SystemExit(f"{tf}: LEAN win/loss totals do not reconcile with trade details.")

        out_dir = output_root / tf
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (out_dir / "python_baseline.json").write_text(
            json.dumps(item.get("python_baseline", {}), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        (out_dir / "lean_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        timeframes.append({
            "metadata": metadata,
            "python_baseline": item.get("python_baseline", {}),
            "lean_report": report,
        })

    if not timeframes:
        raise SystemExit("No real historical timeframe reports were generated.")

    summary = {
        "report_type": "LEAN real historical market backtest",
        "market_data_type": "real_historical_ohlc",
        "synthetic_fixture": False,
        "request_id": args.request_id,
        "asset_key": args.asset,
        "engine": "QuantConnect LEAN open-source engine",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "timeframes": timeframes,
        "disclaimer": (
            "The market OHLC data is historical provider data. Gold volume is the project's "
            "neutral proxy, not traded volume. Fees are an explicit 0.05R round-trip assumption, "
            "not broker-specific commissions or spread."
        ),
    }
    (output_root / "lean-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
