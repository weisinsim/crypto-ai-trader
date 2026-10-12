"""Bybit V5 public historical linear-contract candle adapter."""
from __future__ import annotations

import time
from math import isfinite
import httpx

BASE_URL = "https://api.bybit.com"


def download_bybit_candles(symbol: str, interval: str, start_ms: int, end_ms: int, *,
                           base_url: str = BASE_URL, pause_seconds: float = 0.05) -> list[dict]:
    """Download closed Bybit candles in [start_ms, end_ms); output project CSV schema."""
    if not symbol or not symbol.strip().isalnum():
        raise ValueError("symbol must be an alphanumeric Bybit symbol")
    if interval not in {"1", "3", "5", "15", "30", "60", "120", "240", "360", "720", "D", "W", "M"}:
        raise ValueError("unsupported Bybit interval")
    if start_ms < 0 or end_ms <= start_ms:
        raise ValueError("require 0 <= start_ms < end_ms")
    records = []
    page_end = end_ms - 1
    with httpx.Client(timeout=20.0) as client:
        while page_end >= start_ms:
            response = client.get(base_url.rstrip("/") + "/v5/market/kline", params={
                "category": "linear", "symbol": symbol.upper(), "interval": interval,
                "start": start_ms, "end": page_end, "limit": 1000,
            })
            response.raise_for_status()
            payload = response.json()
            if payload.get("retCode") != 0:
                raise ValueError(f"Bybit kline API error: {payload.get('retCode')} {payload.get('retMsg')}")
            page = payload.get("result", {}).get("list", [])
            if not page:
                break
            for item in page:
                ts = int(item[0])
                values = [float(v) for v in item[1:6]]
                if not all(isfinite(v) for v in values) or min(values[:4]) <= 0 or values[4] < 0:
                    raise ValueError(f"invalid Bybit candle at {ts}")
                if start_ms <= ts < end_ms:
                    # Bybit's list is reverse chronological; schema is normalized to UTC ms and OHLCV.
                    records.append({"t": ts, "o": values[0], "h": values[1], "l": values[2],
                                    "c": values[3], "v": values[4], "q": 0.0, "venue": "bybit"})
            oldest = min(int(item[0]) for item in page)
            if len(page) < 1000:
                break
            next_end = oldest - 1
            if next_end >= page_end:
                raise RuntimeError("Bybit candle pagination did not move backward")
            page_end = next_end
            if pause_seconds:
                time.sleep(pause_seconds)
    records.sort(key=lambda row: row["t"])
    if len({row["t"] for row in records}) != len(records):
        raise ValueError("duplicate Bybit candle timestamps")
    return records
