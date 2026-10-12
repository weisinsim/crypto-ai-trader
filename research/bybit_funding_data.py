"""Bybit V5 public funding-rate and mark-price data adapter.

This adapter is venue-specific. Pair its output only with Bybit linear-contract
candles; do not mix it silently with Binance candles or funding records.
"""
from __future__ import annotations

import time
from math import isfinite
import httpx

BASE_URL = "https://api.bybit.com"


def download_bybit_funding(symbol: str, start_ms: int, end_ms: int, *,
                           base_url: str = BASE_URL, pause_seconds: float = 0.05) -> list[dict]:
    """Return verified Bybit funding events in [start_ms, end_ms), with mark-kline open provenance."""
    if not symbol or not symbol.strip().isalnum():
        raise ValueError("symbol must be an alphanumeric Bybit symbol")
    if start_ms < 0 or end_ms <= start_ms:
        raise ValueError("require 0 <= start_ms < end_ms")
    records = []
    with httpx.Client(timeout=20.0) as client:
        cursor = start_ms
        while cursor < end_ms:
            response = client.get(base_url.rstrip("/") + "/v5/market/funding/history", params={
                "category": "linear", "symbol": symbol.upper(),
                "startTime": cursor, "endTime": end_ms - 1, "limit": 200,
            })
            response.raise_for_status()
            payload = response.json()
            if payload.get("retCode") != 0:
                raise ValueError(f"Bybit funding API error: {payload.get('retCode')} {payload.get('retMsg')}")
            page = payload.get("result", {}).get("list", [])
            if not page:
                break
            parsed = []
            for item in page:
                ts = int(item["fundingRateTimestamp"])
                rate = float(item["fundingRate"])
                if not isfinite(rate):
                    raise ValueError(f"invalid Bybit funding rate at {ts}")
                if start_ms <= ts < end_ms:
                    parsed.append((ts, rate))
            records.extend(parsed)
            next_cursor = max(int(item["fundingRateTimestamp"]) for item in page) + 1
            if next_cursor <= cursor:
                raise RuntimeError("Bybit funding pagination did not advance")
            cursor = next_cursor
            if len(page) < 200:
                break
            if pause_seconds:
                time.sleep(pause_seconds)

        # Fetch official Bybit mark-price candles around every funding timestamp.
        # This records an explicit venue-specific source; it is not Binance mark data.
        result = []
        for ts, rate in sorted(set(records)):
            response = client.get(BASE_URL.rstrip("/") + "/v5/market/mark-price-kline", params={
                "category": "linear", "symbol": symbol.upper(), "interval": "1",
                "start": ts, "end": ts + 60_000, "limit": 2,
            })
            response.raise_for_status()
            payload = response.json()
            if payload.get("retCode") != 0:
                raise ValueError(f"Bybit mark-kline API error: {payload.get('retCode')} {payload.get('retMsg')}")
            candles = payload.get("result", {}).get("list", [])
            match = next((c for c in candles if int(c[0]) == ts), None)
            if match is None:
                raise ValueError(f"missing exact Bybit mark-price candle at funding timestamp {ts}")
            mark_price = float(match[1])
            if not isfinite(mark_price) or mark_price <= 0:
                raise ValueError(f"invalid Bybit mark price at {ts}")
            result.append({
                "funding_time": ts, "funding_rate": rate, "mark_price": mark_price,
                "symbol": symbol.upper(), "mark_price_source": "bybit_mark_kline_open",
                "venue": "bybit",
            })
    result.sort(key=lambda row: row["funding_time"])
    if len({row["funding_time"] for row in result}) != len(result):
        raise ValueError("duplicate Bybit funding timestamps")
    return result
