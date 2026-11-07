"""Exchange-agnostic pagination and page-payload audit for funding imports.

This validates a normalized page envelope supplied by an exchange-specific
adapter. It does not fetch pages or parse raw JSON. When the caller supplies
events independently parsed from each raw response, it compares those events
with the normalized rows; the audit cannot verify that parser provenance itself
is genuine or prove that the adapter requested every page.
"""
from __future__ import annotations

import hashlib
import math


def _is_finite_number(value):
    """Return False for numeric values that cannot be represented finitely."""
    try:
        return math.isfinite(float(value))
    except (OverflowError, TypeError, ValueError):
        return False


def audit_funding_pages(pages, *, symbol, start_ts_ms, end_ts_ms,
                        expected_first_cursor=None,
                        parsed_raw_events_by_page=None):
    """Audit ordered page envelopes, raw-byte digests, and normalized events.

    Each page must contain: page_index (0-based), request_cursor, next_cursor,
    raw_payload (exact response bytes), and events (normalized funding rows).
    The first request cursor may be pinned with expected_first_cursor. For a
    cursor-based API, each page's next_cursor must equal the next page's
    request_cursor, and the final next_cursor must be None.

    A PASS without parsed_raw_events_by_page means envelope consistency only.
    When supplied, parsed_raw_events_by_page must be a parallel list of event
    lists independently parsed from each raw payload by a source-specific
    parser. The parsed rows are compared to normalized events by symbol,
    timestamp, and numeric funding rate.
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
    if parsed_raw_events_by_page is not None and not isinstance(parsed_raw_events_by_page, (list, tuple)):
        raise ValueError("parsed_raw_events_by_page must be a list or tuple when supplied")

    errors = []
    page_reports = []
    all_events = []
    seen_timestamps = set()
    seen_request_cursors = []
    previous_cursor = expected_first_cursor
    normalization_verified = parsed_raw_events_by_page is not None
    if parsed_raw_events_by_page is not None and len(parsed_raw_events_by_page) != len(pages):
        errors.append("normalization_page_count_mismatch")
        normalization_verified = False

    for index, page in enumerate(pages):
        if not isinstance(page, dict):
            errors.append(f"page_{index}:not_a_mapping")
            normalization_verified = False
            continue
        if page.get("page_index") != index or isinstance(page.get("page_index"), bool):
            errors.append(f"page_{index}:index_mismatch")
        request_cursor = page.get("request_cursor")
        if request_cursor is not None and (
            isinstance(request_cursor, bool) or not isinstance(request_cursor, (str, int))
        ):
            errors.append(f"page_{index}:invalid_request_cursor_type")
        if index == 0 and expected_first_cursor is not None and request_cursor != expected_first_cursor:
            errors.append("first_request_cursor_mismatch")
        if index > 0 and request_cursor != previous_cursor:
            errors.append(f"page_{index}:cursor_chain_mismatch")
        if any(request_cursor == prior for prior in seen_request_cursors):
            errors.append(f"page_{index}:repeated_request_cursor")
        seen_request_cursors.append(request_cursor)
        next_cursor = page.get("next_cursor")
        if next_cursor is not None and (
            isinstance(next_cursor, bool) or not isinstance(next_cursor, (str, int))
        ):
            errors.append(f"page_{index}:invalid_next_cursor_type")
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
            # Without the exact response bytes, a supplied parser result cannot
            # be tied to an auditable page artifact. Do not claim normalization
            # verification for this page.
            if parsed_raw_events_by_page is not None:
                normalization_verified = False
        elif not raw_payload:
            errors.append(f"page_{index}:raw_payload_empty")
            digest = hashlib.sha256(raw_payload).hexdigest()
            if parsed_raw_events_by_page is not None:
                normalization_verified = False
        else:
            digest = hashlib.sha256(raw_payload).hexdigest()
        events = page.get("events")
        if not isinstance(events, (list, tuple)):
            errors.append(f"page_{index}:events_not_a_sequence")
            events = []

        page_event_count = 0
        normalized_keys = []
        for event_index, event in enumerate(events):
            if not isinstance(event, dict):
                errors.append(f"page_{index}:event_{event_index}_not_a_mapping")
                if parsed_raw_events_by_page is not None:
                    normalization_verified = False
                continue
            event_symbol = event.get("symbol")
            ts = event.get("funding_timestamp_ms")
            rate = event.get("funding_rate")
            if not isinstance(event_symbol, str) or event_symbol.strip().upper() != symbol.strip().upper():
                errors.append(f"page_{index}:event_{event_index}_symbol_mismatch")
                if parsed_raw_events_by_page is not None:
                    normalization_verified = False
                continue
            if isinstance(ts, bool) or not isinstance(ts, int) or ts < 0:
                errors.append(f"page_{index}:event_{event_index}_invalid_timestamp")
                if parsed_raw_events_by_page is not None:
                    normalization_verified = False
                continue
            if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not _is_finite_number(rate):
                errors.append(f"page_{index}:event_{event_index}_invalid_rate")
                if parsed_raw_events_by_page is not None:
                    normalization_verified = False
                continue
            normalized_keys.append((event_symbol.strip().upper(), ts, float(rate)))
            if ts in seen_timestamps:
                errors.append(f"duplicate_funding_timestamp:{ts}")
                continue
            seen_timestamps.add(ts)
            all_events.append({"symbol": event_symbol.strip().upper(),
                               "funding_timestamp_ms": ts,
                               "funding_rate": float(rate)})
            page_event_count += 1

        if parsed_raw_events_by_page is not None and index < len(parsed_raw_events_by_page):
            raw_events = parsed_raw_events_by_page[index]
            if not isinstance(raw_events, (list, tuple)):
                errors.append(f"page_{index}:parsed_raw_events_not_a_sequence")
                normalization_verified = False
            else:
                raw_keys = []
                for raw_event in raw_events:
                    if not isinstance(raw_event, dict):
                        raw_keys.append(None)
                        continue
                    raw_symbol = raw_event.get("symbol")
                    raw_ts = raw_event.get("funding_timestamp_ms")
                    raw_rate = raw_event.get("funding_rate")
                    if (not isinstance(raw_symbol, str)
                        or raw_symbol.strip().upper() != symbol.strip().upper()
                        or isinstance(raw_ts, bool) or not isinstance(raw_ts, int) or raw_ts < 0
                        or isinstance(raw_rate, bool) or not isinstance(raw_rate, (int, float))
                        or not _is_finite_number(raw_rate)):
                        raw_keys.append(None)
                    else:
                        raw_keys.append((raw_symbol.strip().upper(), raw_ts, float(raw_rate)))
                if any(key is None for key in raw_keys):
                    errors.append(f"page_{index}:invalid_parsed_raw_event")
                    normalization_verified = False
                elif raw_keys != normalized_keys:
                    errors.append(f"page_{index}:normalized_events_do_not_match_raw")
                    normalization_verified = False

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
        "normalization_verified": normalization_verified and not any(
            "normalization" in error or "normalized_events_do_not_match_raw" in error
            or "parsed_raw_events" in error or "invalid_parsed_raw_event" in error
            for error in errors
        ),
        "normalized_events_match_raw_payload": normalization_verified and not any(
            "normalization" in error or "normalized_events_do_not_match_raw" in error
            or "parsed_raw_events" in error or "invalid_parsed_raw_event" in error
            for error in errors
        ),
        "source_verified": False,
        "event_coverage_verified": False,
        "auto_trade_authorized": False,
        "warning": "Raw bytes are hashed. Normalization is verified only when independently parsed raw events are supplied and match; exchange provenance and API event completeness remain unverified.",
    }
