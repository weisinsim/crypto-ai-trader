"""Read-only recovery orchestration with explicit independent evidence gates.

No live exchange schema is assumed. The injected source must return normalized
pages plus independently established terminal/interval coverage evidence.
No checkpoints, alerts, orders or deployment actions are performed here.
"""
from __future__ import annotations

import inspect

from notifications.pionex_recovery_window import recovery_window
from notifications.pionex_recovery_batch import prepare_recovery_batch


class RecoveryRunnerError(ValueError):
    pass


async def run_recovery_once(last_verified_ms, now_ms, *, expected_symbol,
                            fetch_verified_pages, overlap_ms=300000,
                            max_span_ms=3600000, max_pages=100,
                            max_fills=10000):
    """Fetch and validate one bounded interval, leaving all side effects disabled.

    fetch_verified_pages(symbol=..., start_ms=..., end_ms=..., max_pages=...,
                         max_fills=...) must be an async callable returning:
      {pages: list, terminal_page_verified: bool,
       interval_coverage_verified: bool}
    Evidence flags must be independently derived, never inferred from a
    successful HTTP response or simply from an empty page.
    """
    if not callable(fetch_verified_pages):
        raise RecoveryRunnerError("Async read-only page source required")
    window = recovery_window(last_verified_ms, now_ms,
                             overlap_ms=overlap_ms, max_span_ms=max_span_ms)
    result = fetch_verified_pages(
        symbol=expected_symbol, start_ms=window["start_ms"],
        end_ms=window["end_ms"], max_pages=max_pages, max_fills=max_fills)
    if not inspect.isawaitable(result):
        raise RecoveryRunnerError("Page source must be asynchronous")
    evidence = await result
    if not isinstance(evidence, dict) or set(evidence) != {
            "pages", "terminal_page_verified", "interval_coverage_verified"}:
        raise RecoveryRunnerError("Incomplete or unexpected source evidence")
    return prepare_recovery_batch(
        last_verified_ms, now_ms, evidence["pages"],
        expected_symbol=expected_symbol,
        terminal_page_verified=evidence["terminal_page_verified"],
        interval_coverage_verified=evidence["interval_coverage_verified"],
        overlap_ms=overlap_ms, max_span_ms=max_span_ms,
        max_pages=max_pages, max_fills=max_fills)
