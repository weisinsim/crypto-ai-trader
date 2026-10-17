"""Dependency-free health gate for dashboard market-data freshness."""
from __future__ import annotations


def build_health_payload(
    now: float,
    symbol_cache: dict,
    symbols: list[str],
    status: str,
    source: str | None,
) -> dict:
    """Build a fail-closed health snapshot for every configured symbol."""
    stale_after_sec = 180
    ages = {
        symbol: (
            round(now - symbol_cache[symbol]["ts"], 1)
            if symbol_cache[symbol].get("ts") else None
        )
        for symbol in symbols
    }
    stale_symbols = [
        symbol for symbol, age in ages.items()
        if age is None or age > stale_after_sec
    ]
    ready = sum(symbol_cache[symbol].get("price") is not None for symbol in symbols)
    analyzed = sum(symbol_cache[symbol].get("analysis_ts", 0) > 0 for symbol in symbols)
    healthy = ready == len(symbols) and analyzed == len(symbols) and not stale_symbols
    return {
        "ok": healthy,
        "status": status,
        "symbols_ready": ready,
        "symbols_total": len(symbols),
        "analysis_ready": analyzed,
        "analysis_total": len(symbols),
        "stale_after_sec": stale_after_sec,
        "age_sec": max((age for age in ages.values() if age is not None), default=None),
        "symbol_age_sec": ages,
        "stale_symbols": stale_symbols,
        "source": source,
    }
