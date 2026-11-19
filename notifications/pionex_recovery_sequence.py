"""Bounded read-only recovery sequence, never advancing persistent state.

A caller must supply a previously VERIFIED checkpoint. Each window is fetched
and independently validated; failure aborts the sequence without emitting any
notifications or writing any checkpoints. The returned candidate boundary is
NOT authorization to persist a watermark.
"""
from __future__ import annotations

from notifications.pionex_recovery_runner import run_recovery_once


class RecoverySequenceError(ValueError):
    pass


async def inspect_recovery_sequence(last_verified_ms, now_ms, *,
                                    expected_symbol, fetch_verified_pages,
                                    max_windows=4, overlap_ms=300000,
                                    max_span_ms=3600000, max_pages=100,
                                    max_fills=10000, source_timeout_seconds=15):
    if type(max_windows) is not int or not 1 <= max_windows <= 100:
        raise RecoverySequenceError("Invalid recovery window cap")
    if type(last_verified_ms) is not int or type(now_ms) is not int:
        raise RecoverySequenceError("Verified checkpoint and clock required")
    if last_verified_ms > now_ms:
        raise RecoverySequenceError("Checkpoint in future")
    cursor = last_verified_ms
    windows = []
    seen_fill_ids = set()
    for _ in range(max_windows):
        batch = await run_recovery_once(
            cursor, now_ms, expected_symbol=expected_symbol,
            fetch_verified_pages=fetch_verified_pages,
            overlap_ms=overlap_ms, max_span_ms=max_span_ms,
            max_pages=max_pages, max_fills=max_fills,
            source_timeout_seconds=source_timeout_seconds)
        window = batch["window"]
        if window["end_ms"] < cursor:
            raise RecoverySequenceError("Recovery regressed")
        if window["end_ms"] == cursor and window["more_history_pending"]:
            raise RecoverySequenceError("Recovery made no forward progress")
        # Overlapping windows may legitimately return the same exchange fill.
        # Deduplicate across the entire sequence, not only within each page.
        unique_fills = []
        for fill in batch["fills"]:
            fill_id = str(fill["fill_id"])
            if fill_id not in seen_fill_ids:
                seen_fill_ids.add(fill_id)
                unique_fills.append(fill)
        batch["fills"] = unique_fills
        windows.append(batch)
        cursor = window["end_ms"]
        if not window["more_history_pending"]:
            break
    return {
        "batches": windows,
        "candidate_end_ms": cursor,
        "more_history_pending": cursor < now_ms,
        "checkpoint_advanced": False,
        "notification_ready": False,
    }
