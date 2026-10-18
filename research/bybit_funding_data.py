"""Bybit V5 public funding-rate and mark-price data adapter.

Pair these records only with Bybit linear-contract candles; never silently mix venues.
"""
from __future__ import annotations

import time
from math import isfinite
import httpx

# Bybit documents api.bytick.com as an alternative REST hostname.
BASE_URL = "https://api.bytick.com"


def download_bybit_funding(symbol: str, start_ms: int, end_ms: int, *,
                           base_url: str = BASE_URL, pause_seconds: float = 0.05) -> list[dict]:
    """Return verified Bybit funding events in [start_ms, end_ms), with mark-kline open provenance."""
    if not isinstance(symbol, str) or not symbol.strip() or not symbol.strip().isalnum():
        raise ValueError("symbol must be a non-empty alphanumeric Bybit symbol")
    if (isinstance(start_ms, bool) or not isinstance(start_ms, int)
            or isinstance(end_ms, bool) or not isinstance(end_ms, int)
            or start_ms < 0 or end_ms <= start_ms):
        raise ValueError("require integer timestamps with 0 <= start_ms < end_ms")
    if (isinstance(pause_seconds, bool) or not isinstance(pause_seconds, (int, float))
            or not isfinite(pause_seconds) or pause_seconds < 0):
        raise ValueError("pause_seconds must be a finite non-negative number")
    records = []
    with httpx.Client(timeout=20.0) as client:
        page_end = end_ms - 1
        while page_end >= start_ms:
            response = client.get(base_url.rstrip("/") + "/v5/market/funding/history", params={
                "category": "linear", "symbol": symbol.upper(),
                "startTime": start_ms, "endTime": page_end, "limit": 200,
            })
            response.raise_for_status()
            payload = response.json()
            if payload.get("retCode") != 0:
                raise ValueError(f"Bybit funding API error: {payload.get('retCode')} {payload.get('retMsg')}")
            page = payload.get("result", {}).get("list", [])
            if not page:
                break
            if not isinstance(page, list):
                raise ValueError("Bybit funding response list must be a list")
            parsed_page = []
            for index, item in enumerate(page):
                if not isinstance(item, dict):
                    raise ValueError(f"Bybit funding row {index} must be a mapping")
                missing = [key for key in ("fundingRateTimestamp", "fundingRate") if key not in item]
                if missing:
                    raise ValueError(f"Bybit funding row {index} missing required fields: {missing}")
                raw_ts, raw_rate = item["fundingRateTimestamp"], item["fundingRate"]
                if isinstance(raw_ts, bool) or not (
                    isinstance(raw_ts, int) or
                    (isinstance(raw_ts, str) and raw_ts.strip().isdigit())
                ):
                    raise ValueError(f"invalid Bybit funding timestamp in row {index}")
                ts = int(raw_ts)
                if isinstance(raw_rate, bool):
                    raise ValueError(f"invalid Bybit funding rate at {ts}")
                try:
                    rate = float(raw_rate)
                except (TypeError, ValueError, OverflowError) as exc:
                    raise ValueError(f"invalid Bybit funding rate at {ts}") from exc
                if not isfinite(rate):
                    raise ValueError(f"invalid Bybit funding rate at {ts}")
                parsed_page.append(ts)
                if start_ms <= ts < end_ms:
                    records.append((ts, rate))
            oldest_ts = min(parsed_page)
            if len(page) < 200:
                break
            next_end = oldest_ts - 1
            if next_end >= page_end:
                raise RuntimeError("Bybit funding pagination did not move backward")
            page_end = next_end
            if pause_seconds:
                time.sleep(pause_seconds)

        result = []
        for ts, rate in sorted(set(records)):
            response = client.get(base_url.rstrip("/") + "/v5/market/mark-price-kline", params={
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
            if pause_seconds:
                time.sleep(pause_seconds)
    result.sort(key=lambda row: row["funding_time"])
    if len({row["funding_time"] for row in result}) != len(result):
        raise ValueError("duplicate Bybit funding timestamps")
    return result
