"""OKX V5 historical swap funding and official mark-price candle adapter."""
from __future__ import annotations
import time
from math import isfinite
import httpx
from research.okx_candle_data import BASE_URL, INSTRUMENTS


def download_okx_funding(symbol: str, start_ms: int, end_ms: int, *,
                         base_url: str = BASE_URL, pause_seconds: float = 0.12) -> list[dict]:
    """Download realized OKX funding events and require exact-time official mark-candle opens."""
    if not isinstance(symbol, str) or symbol not in INSTRUMENTS:
        raise ValueError(f"unsupported OKX confirmation symbol: {symbol}")
    if (isinstance(start_ms, bool) or not isinstance(start_ms, int)
            or isinstance(end_ms, bool) or not isinstance(end_ms, int)
            or start_ms < 0 or end_ms <= start_ms):
        raise ValueError("require integer timestamps with 0 <= start_ms < end_ms")
    if (isinstance(pause_seconds, bool) or not isinstance(pause_seconds, (int, float))
            or not isfinite(pause_seconds) or pause_seconds < 0):
        raise ValueError("pause_seconds must be a finite non-negative number")
    inst_id = INSTRUMENTS[symbol]
    raw = []
    cursor = end_ms
    with httpx.Client(timeout=20.0) as client:
        while cursor > start_ms:
            response = client.get(base_url.rstrip("/") + "/api/v5/public/funding-rate-history", params={
                "instId": inst_id, "after": cursor, "limit": "400"
            })
            response.raise_for_status()
            payload = response.json()
            if payload.get("code") != "0":
                raise ValueError(f"OKX funding API error: {payload.get('code')} {payload.get('msg')}")
            page = payload.get("data", [])
            if not page:
                break
            if not isinstance(page, list):
                raise ValueError("OKX funding response data must be a list")
            parsed_page = []
            for index, item in enumerate(page):
                if not isinstance(item, dict):
                    raise ValueError(f"OKX funding row {index} must be a mapping")
                if "fundingTime" not in item:
                    raise ValueError(f"OKX funding row {index} missing required field: fundingTime")
                rate_key = "realizedRate" if "realizedRate" in item else "fundingRate"
                if rate_key not in item:
                    raise ValueError(f"OKX funding row {index} missing realizedRate/fundingRate")
                raw_ts, raw_rate = item["fundingTime"], item[rate_key]
                if isinstance(raw_ts, bool) or not (
                    isinstance(raw_ts, int) or
                    (isinstance(raw_ts, str) and raw_ts.strip().isdigit())
                ):
                    raise ValueError(f"invalid OKX funding timestamp in row {index}")
                ts = int(raw_ts)
                if isinstance(raw_rate, bool):
                    raise ValueError(f"invalid OKX funding rate at {ts}")
                try:
                    rate = float(raw_rate)
                except (TypeError, ValueError, OverflowError) as exc:
                    raise ValueError(f"invalid OKX funding rate at {ts}") from exc
                if not isfinite(rate):
                    raise ValueError(f"invalid OKX funding rate at {ts}")
                parsed_page.append(ts)
                if start_ms <= ts < end_ms:
                    raw.append((ts, rate))
            oldest = min(parsed_page)
            if oldest >= cursor:
                raise RuntimeError("OKX funding pagination did not move backward")
            if oldest <= start_ms:
                break
            cursor = oldest
            if pause_seconds:
                time.sleep(pause_seconds)
        result = []
        for ts, rate in sorted(set(raw)):
            response = client.get(base_url.rstrip("/") + "/api/v5/market/history-mark-price-candles", params={
                "instId": inst_id, "bar": "1m", "after": ts + 60_000, "limit": "100"
            })
            response.raise_for_status()
            payload = response.json()
            if payload.get("code") != "0":
                raise ValueError(f"OKX mark-price history API error: {payload.get('code')} {payload.get('msg')}")
            match = next((c for c in payload.get("data", []) if int(c[0]) == ts), None)
            if match is None:
                raise ValueError(f"missing exact OKX mark-price candle at funding timestamp {ts}")
            mark = float(match[1])
            if not isfinite(mark) or mark <= 0:
                raise ValueError(f"invalid OKX mark price at {ts}")
            result.append({"funding_time":ts,"funding_rate":rate,"mark_price":mark,
                           "symbol":symbol,"mark_price_source":"okx_mark_price_kline_open",
                           "venue":"okx","instrument_id":inst_id})
            if pause_seconds:
                time.sleep(pause_seconds)
    result.sort(key=lambda x:x["funding_time"])
    if len({x["funding_time"] for x in result}) != len(result):
        raise ValueError("duplicate OKX funding timestamps")
    return result
