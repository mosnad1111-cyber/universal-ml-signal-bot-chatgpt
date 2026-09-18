"""Optional keyless XAUS provider for live gold price fallback.

XAUS provides a real XAU/USD spot quote and a short recorded intraday series.
It is not a replacement for historical OHLCV data needed for model training.
"""
import time
from datetime import datetime, timezone
import requests
import pandas as pd

BASE_URL = "https://xaus.com/api/v1"
TIMEOUT = 12


def fetch_spot() -> float:
    """Return the latest real XAU/USD ounce price; never fabricate a price."""
    response = requests.get(
        f"{BASE_URL}/spot",
        params={"compact": "1", "fresh": int(time.time())},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    price = payload.get("spot_usd_oz")
    state = payload.get("data_state") or {}
    if price is None or state.get("status") == "unavailable":
        raise RuntimeError("XAUS did not return an available gold spot price")
    return float(price)


def fetch_intraday(hours: int = 48) -> pd.DataFrame:
    """Return XAUS's recorded 2-minute price series as OHLCV-like data."""
    hours = max(1, min(int(hours), 48))
    response = requests.get(
        f"{BASE_URL}/intraday",
        params={"symbol": "xau", "hours": hours, "fresh": int(time.time())},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    points = payload.get("points") or []
    rows = []
    for point in points:
        timestamp = point.get("t")
        price = point.get("p")
        if timestamp is None or price is None:
            continue
        rows.append((pd.to_datetime(timestamp, utc=True), float(price)))
    if not rows:
        raise RuntimeError("XAUS returned no intraday gold points")
    frame = pd.DataFrame(rows, columns=["Timestamp", "Close"]).set_index("Timestamp")
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    frame["Open"] = frame["Close"]
    frame["High"] = frame["Close"]
    frame["Low"] = frame["Close"]
    frame["Volume"] = 0.0
    return frame[["Open", "High", "Low", "Close", "Volume"]]


def healthcheck() -> dict:
    """Return a small diagnostic payload without exposing implementation errors."""
    try:
        price = fetch_spot()
        return {"ok": True, "price": price, "source": "XAUS"}
    except Exception as exc:
        return {"ok": False, "source": "XAUS", "error": str(exc)}
