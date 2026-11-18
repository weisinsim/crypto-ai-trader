"""Offline recovery batch preparation; no HTTP, notifications, or trading.

The caller must independently validate the exchange response schema and verify
that the requested time window is completely covered before checkpoint advance.
"""
from __future__ import annotations

from notifications.pionex_fill_pages import collect_verified_fill_pages
from notifications.pionex_recovery_window import recovery_window


class RecoveryBatchError(ValueError):
    pass


def prepare_recovery_batch(last_verified_ms, now_ms, pages, *,
                           expected_symbol, terminal_page_verified=False,
                           interval_coverage_verified=False,
                           overlap_ms=300000, max_span_ms=3600000):
    """Return a bounded, uncommitted batch of normalized fills.

    This function deliberately cannot advance a checkpoint. Even a complete
    pagination chain is insufficient evidence of full interval coverage.
    """
    window = recovery_window(last_verified_ms, now_ms,
                             overlap_ms=overlap_ms, max_span_ms=max_span_ms)
    if terminal_page_verified is not True:
        raise RecoveryBatchError("Terminal page not independently verified")
    if interval_coverage_verified is not True:
        raise RecoveryBatchError("Requested interval coverage not verified")
    fills = collect_verified_fill_pages(
        pages, expected_symbol=expected_symbol, complete=True)
    for fill in fills:
        timestamp = fill.get("timestamp_ms")
        if type(timestamp) is not int:
            raise RecoveryBatchError("Normalized fill timestamp missing")
        if not window["start_ms"] <= timestamp <= window["end_ms"]:
            raise RecoveryBatchError("Fill outside verified recovery window")
    return {"window": window, "fills": fills, "checkpoint_advanced": False,
            "notification_ready": False}
