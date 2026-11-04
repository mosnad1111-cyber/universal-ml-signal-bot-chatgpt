import csv
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
bars_dir = root / "bars"
bars_dir.mkdir(parents=True, exist_ok=True)

bars = [
    ("20240102 00:00:00", 100, 101, 99, 100, 10, 5),
    ("20240102 00:05:00", 100, 101, 99, 100, 10, 5),
    ("20240102 00:10:00", 100, 105, 99, 104, 10, 5),
    ("20240102 00:15:00", 100, 103, 99, 101, 10, 5),
    ("20240102 00:20:00", 100, 104, 96, 100, 10, 5),
]
with (bars_dir / "20240102.csv").open("w", newline="", encoding="utf-8") as handle:
    writer = csv.writer(handle)
    writer.writerow(("time", "open", "high", "low", "close", "volume", "period_minutes"))
    writer.writerows(bars)

metadata = {
    "request_id": "lean-smoke-fixture",
    "asset_key": "GOLD",
    "symbol": "TVC:GOLD",
    "asset_label": "Gold",
    "timeframe": "5m",
    "period_minutes": 5,
    "start": "2024-01-02T00:00:00+00:00",
    "end": "2024-01-02T00:20:00+00:00",
    "fee_r": 0.05,
    "bars_count": len(bars),
    "signals_count": 2,
}
(root / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

signals = [
    {
        "signal_time": "2024-01-02T00:00:00+00:00",
        "direction": "BUY",
        "entry": 100,
        "sl": 98,
        "tp": 104,
        "risk": 2,
        "score": 80,
        "ai_prob": 0.8,
    },
    {
        "signal_time": "2024-01-02T00:15:00+00:00",
        "direction": "SELL",
        "entry": 101,
        "sl": 103,
        "tp": 97,
        "risk": 2,
        "score": 80,
        "ai_prob": 0.8,
    },
]
(root / "signals.json").write_text(json.dumps(signals), encoding="utf-8")
