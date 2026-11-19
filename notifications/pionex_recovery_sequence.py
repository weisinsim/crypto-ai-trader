"""Bounded read-only recovery sequence, never advancing persistent state.

A caller must supply a previously VERIFIED checkpoint. Each window is fetched
and independently validated; failure aborts the sequence without emitting any
notifications or writing any checkpoints. The returned candidate boundary is
NOT authorization to persist a watermark.
"""
from __future__ import annotations

import asyncio
import math

from notifications.pionex_recovery_runner import run_recovery_once


class RecoverySequenceError(ValueError):
    pass


async def inspect_recovery_sequence(last_verified_ms, now_ms, *,
                                    expected_symbol, fetch_verified_pages,
                                    max_windows=4, overlap_ms=300000,
                                    max_span_ms=3600000, max_pages=100,
                                    max_fills=10000, source_timeout_seconds=15,
                                    total_timeout_seconds=30):
    if type(max_windows) is not int or not 1 <= max_windows <= 100:
        raise RecoverySequenceError("Invalid recovery window cap")
    if type(last_verified_ms) is not int or type(now_ms) is not int:
        raise RecoverySequenceError("Verified checkpoint and clock required")
    if last_verified_ms > now_ms:
        raise RecoverySequenceError("Checkpoint in future")
    if type(max_fills) is not int or max_fills <= 0:
        raise RecoverySequenceError("Invalid sequence fill cap")
    if (type(total_timeout_seconds) not in (int, float)
            or not math.isfinite(total_timeout_seconds)
            or not 0 < total_timeout_seconds <= 600):
        raise RecoverySequenceError("Invalid total recovery timeout")
    deadline = asyncio.get_running_loop().time() + total_timeout_seconds
    cursor = last_verified_ms
    windows = []
    seen_fills = {}
    for _ in range(max_windows):
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise RecoverySequenceError("Total recovery time budget exhausted")
        batch = await run_recovery_once(
            cursor, now_ms, expected_symbol=expected_symbol,
            fetch_verified_pages=fetch_verified_pages,
            overlap_ms=overlap_ms, max_span_ms=max_span_ms,
            max_pages=max_pages, max_fills=max_fills,
            source_timeout_seconds=min(source_timeout_seconds, remaining))
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
            if fill_id in seen_fills:
                if seen_fills[fill_id] != fill:
                    raise RecoverySequenceError(
                        "Conflicting exchange fill ID across recovery windows")
            else:
                if len(seen_fills) >= max_fills:
                    raise RecoverySequenceError(
                        "Total unique fills exceed sequence safety cap")
                seen_fills[fill_id] = fill.copy()
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
