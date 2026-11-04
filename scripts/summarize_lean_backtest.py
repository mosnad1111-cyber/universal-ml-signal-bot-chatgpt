"""Validate and combine real-market LEAN reports for Telegram delivery."""
from __future__ import annotations

import argparse
import json
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
        if int(report.get("bars", -1)) != int(report.get("received_bars", -2)):
            raise SystemExit(f"{tf}: LEAN did not ingest every real OHLC bar.")
        if int(report.get("bars", 0)) != int(metadata.get("bars_count", -1)):
            raise SystemExit(f"{tf}: LEAN input candle count differs from prepared history.")
        if report.get("order_errors"):
            raise SystemExit(f"{tf}: LEAN order execution errors: {report['order_errors']}")
        if float(report.get("lean_total_fees_usd", 0.0)) < 0:
            raise SystemExit(f"{tf}: invalid LEAN fee total.")

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
