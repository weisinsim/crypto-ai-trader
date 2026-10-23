"""Live closed-candle scan bridge for notification-only entry alerts.

Reads public Binance USD-M klines, asks an injected strategy/dashboard provider
for its signal snapshot, and queues only signals tied to the latest closed
candle. This module does not calculate a strategy signal or place orders.
"""
from __future__ import annotations

import json
import math
import re
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from execution.entry_alerts import build_entry_alert


class MarketDataError(RuntimeError):
    """Raised when public market data cannot be trusted."""


class BinanceUsdMMarketData:
    """Minimal public-only Binance USD-M closed-candle timestamp reader."""

    BASE_URL = "https://fapi.binance.com/fapi/v1/klines"

    def __init__(self, *, timeout=10, opener=urlopen):
        if (not isinstance(timeout, (int, float)) or isinstance(timeout, bool)
                or not math.isfinite(timeout) or timeout <= 0):
            raise ValueError("timeout must be a finite positive number")
        self.timeout = timeout
        self.opener = opener

    def last_closed_candle_ms(self, symbol, interval, *, now_ms=None):
        if not isinstance(symbol, str) or re.fullmatch(r"[A-Z0-9]{2,}USDT", symbol) is None:
            raise ValueError("symbol must be a canonical uppercase USDT pair")
        supported_intervals = {
            "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h",
            "8h", "12h", "1d", "3d", "1w", "1M",
        }
        if not isinstance(interval, str) or interval not in supported_intervals:
            raise ValueError("unsupported_binance_kline_interval")
        if now_ms is not None and (
            isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms <= 0
        ):
            raise ValueError("now_ms must be a positive integer timestamp")
        now_ms = int(time.time() * 1000) if now_ms is None else now_ms
        query = urlencode({"symbol": symbol, "interval": interval, "limit": 2})
        request = Request(f"{self.BASE_URL}?{query}", headers={"User-Agent": "crypto-ai-trader-alert-scanner"})
        try:
            with self.opener(request, timeout=self.timeout) as response:
                rows = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise MarketDataError("binance_market_data_request_failed") from exc
        if not isinstance(rows, list) or not rows:
            raise MarketDataError("binance_market_data_empty")
        valid = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 7:
                raise MarketDataError("binance_market_data_invalid_row")
            open_ms, close_ms = row[0], row[6]
            if (not isinstance(open_ms, int) or isinstance(open_ms, bool)
                    or not isinstance(close_ms, int) or isinstance(close_ms, bool)):
                raise MarketDataError("binance_market_data_invalid_timestamp")
            if close_ms < now_ms:
                # Binance closeTime is inclusive (boundary - 1 ms); normalize
                # to the exclusive candle-close boundary used by replay adapters.
                valid.append((open_ms, close_ms + 1))
        if not valid:
            raise MarketDataError("no_closed_candle_available")
        return max(valid, key=lambda item: item[1])[1]


def scan_once(*, symbols, interval, market_data, snapshot_provider, store,
              now_ms=None, max_age_ms=90 * 60 * 1000):
    """Check symbols once and enqueue fresh, candle-aligned candidate alerts.

    snapshot_provider(symbol) must return the existing strategy/dashboard
    snapshot. A signal is rejected unless its confirmed signal candle exactly
    matches the most recently closed public-market candle, avoiding alerts
    based on stale or still-forming bars. Errors are isolated per symbol.
    """
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    elif isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms <= 0:
        raise ValueError("now_ms must be a positive integer timestamp")
    if (not isinstance(max_age_ms, (int, float)) or isinstance(max_age_ms, bool)
            or not math.isfinite(max_age_ms) or max_age_ms < 0):
        raise ValueError("max_age_ms must be a finite non-negative number")
    if not isinstance(symbols, (list, tuple)) or not symbols:
        raise ValueError("symbols must be a non-empty list or tuple")
    if any(not isinstance(symbol, str)
           or re.fullmatch(r"[A-Z0-9]{2,}USDT", symbol) is None
           for symbol in symbols):
        raise ValueError("symbols must be canonical uppercase USDT pairs")
    if len(set(symbols)) != len(symbols):
        raise ValueError("symbols must not contain duplicates")
    result = {"scanned": 0, "queued": 0, "duplicates": 0, "rejected": 0, "errors": []}
    for symbol in symbols:
        result["scanned"] += 1
        try:
            latest_closed = market_data.last_closed_candle_ms(symbol, interval, now_ms=now_ms)
            snapshot = snapshot_provider(symbol)
            if not isinstance(snapshot, dict):
                result["rejected"] += 1
                continue
            # Never enqueue a signal row returned for a different symbol.
            # The provider is expected to return the requested symbol explicitly.
            if snapshot.get("symbol") != symbol:
                result["rejected"] += 1
                continue
            signal_candle = snapshot.get("confirmed_signal_time")
            if signal_candle is None:
                signal_candle = snapshot.get("last_closed_candle")
            if signal_candle != latest_closed:
                result["rejected"] += 1
                continue
            normalized = dict(snapshot)
            normalized.setdefault("symbol", symbol)
            normalized.setdefault("source", "Binance USD-M closed-candle scan")
            # The dashboard cache uses confirmed_* names, while the alert
            # contract intentionally uses execution-neutral entry/sl/tp fields.
            # Normalize here so a raw dashboard snapshot can be scanned safely.
            field_aliases = {
                "entry": "confirmed_entry",
                "sl": "confirmed_sl",
                "tp1": "confirmed_tp1",
                "tp2": "confirmed_tp2",
                "score": "confirmed_score",
            }
            for target, source in field_aliases.items():
                if normalized.get(target) is None and normalized.get(source) is not None:
                    normalized[target] = normalized[source]
            event = build_entry_alert(normalized, now_ms=now_ms, max_age_ms=max_age_ms)
            if not event["eligible"]:
                result["rejected"] += 1
                continue
            if store.enqueue(event, now=now_ms / 1000):
                result["queued"] += 1
            else:
                result["duplicates"] += 1
        except Exception as exc:
            # Do not expose exception strings, URLs, or any potential credentials.
            result["errors"].append({"symbol": str(symbol), "reason": type(exc).__name__})
    return result
