"""Live closed-candle scan bridge for notification-only entry alerts.

Reads public Binance USD-M klines, asks an injected strategy/dashboard provider
for its signal snapshot, and queues only signals tied to the latest closed
candle. This module does not calculate a strategy signal or place orders.
"""
from __future__ import annotations

import json
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
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise ValueError("timeout must be positive")
        self.timeout = timeout
        self.opener = opener

    def last_closed_candle_ms(self, symbol, interval, *, now_ms=None):
        if not isinstance(symbol, str) or not symbol.endswith("USDT"):
            raise ValueError("symbol must be a USDT pair")
        if not isinstance(interval, str) or not interval:
            raise ValueError("interval is required")
        now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
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
            if not isinstance(open_ms, int) or not isinstance(close_ms, int):
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
    now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    result = {"scanned": 0, "queued": 0, "duplicates": 0, "rejected": 0, "errors": []}
    for symbol in symbols:
        result["scanned"] += 1
        try:
            latest_closed = market_data.last_closed_candle_ms(symbol, interval, now_ms=now_ms)
            snapshot = snapshot_provider(symbol)
            if not isinstance(snapshot, dict):
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
