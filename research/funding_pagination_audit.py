"""Exchange-agnostic pagination and page-payload audit for funding imports.

This validates a normalized page envelope supplied by an exchange-specific
adapter. It does not fetch pages or prove that the adapter requested every page.
"""
from __future__ import annotations

import hashlib
import math


def audit_funding_pages(pages, *, symbol, start_ts_ms, end_ts_ms,
                        expected_first_cursor=None):
    """Audit ordered page envelopes, raw-byte digests, and normalized events.

    Each page must contain: page_index (0-based), request_cursor, next_cursor,
    raw_payload (exact response bytes), and events (normalized funding rows).
    The first request cursor may be pinned with expected_first_cursor. For a
    cursor-based API, each page's next_cursor must equal the next page's
    request_cursor, and the final next_cursor must be None.
    """
    if not isinstance(pages, (list, tuple)) or not pages:
        raise ValueError("pages must be a non-empty list or tuple")
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("symbol must be a non-empty string")
    for name, value in (("start_ts_ms", start_ts_ms), ("end_ts_ms", end_ts_ms)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a nonnegative integer timestamp")
    if end_ts_ms <= start_ts_ms:
        raise ValueError("end_ts_ms must be greater than start_ts_ms")

    errors = []
    page_reports = []
    all_events = []
    seen_timestamps = set()
    seen_request_cursors = []
    previous_cursor = expected_first_cursor

    for index, page in enumerate(pages):
        if not isinstance(page, dict):
            errors.append(f"page_{index}:not_a_mapping")
            continue
        if page.get("page_index") != index or isinstance(page.get("page_index"), bool):
            errors.append(f"page_{index}:index_mismatch")
        request_cursor = page.get("request_cursor")
        if index == 0 and expected_first_cursor is not None and request_cursor != expected_first_cursor:
            errors.append("first_request_cursor_mismatch")
        if index > 0 and request_cursor != previous_cursor:
            errors.append(f"page_{index}:cursor_chain_mismatch")
        if any(request_cursor == prior for prior in seen_request_cursors):
            errors.append(f"page_{index}:repeated_request_cursor")
        seen_request_cursors.append(request_cursor)
        next_cursor = page.get("next_cursor")
        if next_cursor is not None and next_cursor == request_cursor:
            errors.append(f"page_{index}:cursor_did_not_advance")
        if index == len(pages) - 1 and next_cursor is not None:
            errors.append("final_page_has_next_cursor")
        if index < len(pages) - 1 and next_cursor is None:
            errors.append(f"page_{index}:pagination_ended_early")
        previous_cursor = next_cursor

        raw_payload = page.get("raw_payload")
        if not isinstance(raw_payload, bytes):
            errors.append(f"page_{index}:raw_payload_not_bytes")
            digest = None
        else:
            digest = hashlib.sha256(raw_payload).hexdigest()
        events = page.get("events")
        if not isinstance(events, (list, tuple)):
            errors.append(f"page_{index}:events_not_a_sequence")
            events = []

        page_event_count = 0
        for event_index, event in enumerate(events):
            if not isinstance(event, dict):
                errors.append(f"page_{index}:event_{event_index}_not_a_mapping")
                continue
            event_symbol = event.get("symbol")
            ts = event.get("funding_timestamp_ms")
            rate = event.get("funding_rate")
            if not isinstance(event_symbol, str) or event_symbol.strip().upper() != symbol.strip().upper():
                errors.append(f"page_{index}:event_{event_index}_symbol_mismatch")
                continue
            if isinstance(ts, bool) or not isinstance(ts, int) or ts < 0:
                errors.append(f"page_{index}:event_{event_index}_invalid_timestamp")
                continue
            if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not math.isfinite(rate):
                errors.append(f"page_{index}:event_{event_index}_invalid_rate")
                continue
            if ts in seen_timestamps:
                errors.append(f"duplicate_funding_timestamp:{ts}")
                continue
            seen_timestamps.add(ts)
            all_events.append({"symbol": event_symbol.strip().upper(),
                               "funding_timestamp_ms": ts,
                               "funding_rate": float(rate)})
            page_event_count += 1

        page_reports.append({"page_index": index, "raw_payload_sha256": digest,
                             "event_count": page_event_count})

    timestamps = [event["funding_timestamp_ms"] for event in all_events]
    if timestamps != sorted(timestamps):
        errors.append("events_not_globally_sorted")
    in_window = [ts for ts in timestamps if start_ts_ms <= ts < end_ts_ms]
    if len(in_window) != len(timestamps):
        errors.append("events_outside_requested_window")

    return {
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "symbol": symbol.strip().upper(),
        "window_start_ts_ms": start_ts_ms,
        "window_end_ts_ms": end_ts_ms,
        "page_count": len(pages),
        "page_reports": page_reports,
        "event_count": len(all_events),
        "in_window_event_count": len(in_window),
        "events": all_events,
        "pagination_chain_consistent": not any("cursor" in e or "pagination" in e or "final_page" in e for e in errors),
        "raw_page_digests_computed": all(p["raw_payload_sha256"] is not None for p in page_reports),
        "source_verified": False,
        "event_coverage_verified": False,
        "auto_trade_authorized": False,
        "warning": "Audits supplied page envelopes only; completeness still depends on the adapter's source-specific pagination contract and trusted exchange provenance.",
    }
