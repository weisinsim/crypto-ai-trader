"""OKX V5 linear swap historical candles (public market data)."""
from __future__ import annotations
import time
from math import isfinite
import httpx

BASE_URL = "https://www.okx.com"
INSTRUMENTS = {"BTCUSDT": "BTC-USDT-SWAP", "AVAXUSDT": "AVAX-USDT-SWAP"}


def download_okx_candles(symbol: str, start_ms: int, end_ms: int, *,
                         base_url: str = BASE_URL, pause_seconds: float = 0.12) -> list[dict]:
    """Download completed 1H swap candles in [start_ms,end_ms), newest-to-oldest pagination."""
    if symbol not in INSTRUMENTS:
        raise ValueError(f"unsupported OKX confirmation symbol: {symbol}")
    if start_ms < 0 or end_ms <= start_ms:
        raise ValueError("require 0 <= start_ms < end_ms")
    inst_id = INSTRUMENTS[symbol]
    records = []
    cursor = end_ms
    with httpx.Client(timeout=20.0) as client:
        while cursor > start_ms:
            response = client.get(base_url.rstrip("/") + "/api/v5/market/history-candles", params={
                "instId": inst_id, "bar": "1H", "after": cursor, "limit": "300"
            })
            response.raise_for_status()
            payload = response.json()
            if payload.get("code") != "0":
                raise ValueError(f"OKX history-candles API error: {payload.get('code')} {payload.get('msg')}")
            page = payload.get("data", [])
            if not page:
                break
            for item in page:
                ts = int(item[0])
                o, h, l, c, volume = [float(x) for x in item[1:6]]
                if not all(isfinite(x) for x in (o,h,l,c,volume)) or min(o,h,l,c) <= 0 or volume < 0:
                    raise ValueError(f"invalid OKX candle at {ts}")
                if h < max(o,c,l) or l > min(o,c,h):
                    raise ValueError(f"inconsistent OKX OHLC at {ts}")
                # OKX confirms completed candles with field 8 == "1".
                if len(item) > 8 and item[8] != "1":
                    continue
                if start_ms <= ts < end_ms:
                    records.append({"t":ts,"o":o,"h":h,"l":l,"c":c,"v":volume,"q":float(item[7]) if len(item)>7 else 0.0,"venue":"okx"})
            oldest = min(int(item[0]) for item in page)
            if oldest >= cursor:
                raise RuntimeError("OKX candle pagination did not move backward")
            cursor = oldest
            if pause_seconds:
                time.sleep(pause_seconds)
    records.sort(key=lambda x:x["t"])
    times = [x["t"] for x in records]
    if len(times) != len(set(times)):
        raise ValueError("duplicate OKX candle timestamps")
    return records
