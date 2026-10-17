"""Dependency-free health gate for dashboard market-data freshness."""
from __future__ import annotations

from math import isfinite


def _finite_number(value) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and isfinite(value)
    )


def build_health_payload(
    now: float,
    symbol_cache: dict,
    symbols: list[str],
    status: str,
    source: str | None,
) -> dict:
    """Build a fail-closed health snapshot, including malformed cache records."""
    stale_after_sec = 180
    valid_now = _finite_number(now)
    ages = {}
    for symbol in symbols:
        record = symbol_cache.get(symbol) if isinstance(symbol_cache, dict) else None
        timestamp = record.get("ts") if isinstance(record, dict) else None
        if valid_now and _finite_number(timestamp) and timestamp > 0:
            age = now - timestamp
            ages[symbol] = round(age, 1) if isfinite(age) and age >= 0 else None
        else:
            ages[symbol] = None

    stale_symbols = [
        symbol for symbol, age in ages.items()
        if age is None or age > stale_after_sec
    ]
    ready = 0
    analyzed = 0
    for symbol in symbols:
        record = symbol_cache.get(symbol) if isinstance(symbol_cache, dict) else None
        if not isinstance(record, dict):
            continue
        price = record.get("price")
        analysis_ts = record.get("analysis_ts")
        if _finite_number(price) and price > 0:
            ready += 1
        if _finite_number(analysis_ts) and analysis_ts > 0:
            analyzed += 1

    healthy = (
        valid_now
        and ready == len(symbols)
        and analyzed == len(symbols)
        and not stale_symbols
    )
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
