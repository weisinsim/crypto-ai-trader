"""Deterministic CSV importer and coverage audit for historical funding events.

This module does not download data. It ingests a locally supplied CSV export,
normalizes it to the event schema used by funding_history_audit, and records a
content digest. A digest proves file identity, not exchange provenance.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from datetime import datetime, timezone

from research.funding_history_audit import audit_funding_events


def _timestamp_ms(value):
    text = str(value).strip()
    if not text:
        raise ValueError("funding timestamp is required")
    if text.isdigit():
        # CSV exports may use seconds or milliseconds; require an unambiguous unit.
        raise ValueError("numeric timestamps are ambiguous; use ISO-8601 UTC timestamps")
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("funding timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError("funding timestamp must include timezone")
    return int(parsed.astimezone(timezone.utc).timestamp() * 1000)


def import_funding_csv(csv_text, *, symbol, start_ts_ms, end_ts_ms,
                       expected_interval_ms=None, expected_anchor_ts_ms=None):
    """Parse CSV with columns fundingTime and fundingRate (symbol optional).

    Returns normalized events plus an audit manifest. If expected cadence is
    supplied, any missing interval in the requested window is reported, but
    coverage remains unverified until reconciled with the exchange source.
    """
    if not isinstance(csv_text, str) or not csv_text.strip():
        raise ValueError("csv_text must be non-empty text")
    if expected_interval_ms is not None and (
        isinstance(expected_interval_ms, bool) or
        not isinstance(expected_interval_ms, int) or expected_interval_ms <= 0
    ):
        raise ValueError("expected_interval_ms must be a positive integer")
    if expected_anchor_ts_ms is not None and (
        isinstance(expected_anchor_ts_ms, bool) or
        not isinstance(expected_anchor_ts_ms, int) or expected_anchor_ts_ms < 0
    ):
        raise ValueError("expected_anchor_ts_ms must be a nonnegative integer")
    if expected_anchor_ts_ms is not None and expected_interval_ms is None:
        raise ValueError("expected_anchor_ts_ms requires expected_interval_ms")
    raw_bytes = csv_text.encode("utf-8")
    try:
        reader = csv.DictReader(io.StringIO(csv_text))
        required = {"fundingTime", "fundingRate"}
        if reader.fieldnames is None or not required.issubset(set(reader.fieldnames)):
            raise ValueError("CSV requires fundingTime and fundingRate columns")
        events = []
        for line_number, row in enumerate(reader, start=2):
            if not row or all(value is None or not value.strip() for value in row.values()):
                continue
            row_symbol = row.get("symbol")
            if row_symbol and row_symbol.strip().upper() != symbol.strip().upper():
                raise ValueError(f"row {line_number} symbol mismatch")
            rate_text = (row.get("fundingRate") or "").strip()
            try:
                rate = float(rate_text)
            except ValueError as exc:
                raise ValueError(f"row {line_number} fundingRate must be numeric") from exc
            if not math.isfinite(rate):
                raise ValueError(f"row {line_number} fundingRate must be finite")
            events.append({
                "symbol": symbol.strip().upper(),
                "funding_timestamp_ms": _timestamp_ms(row.get("fundingTime", "")),
                "funding_rate": rate,
            })
    except csv.Error as exc:
        raise ValueError("invalid CSV data") from exc

    audit = audit_funding_events(
        events, symbol=symbol, start_ts_ms=start_ts_ms, end_ts_ms=end_ts_ms,
    )
    window_events = [event for event in audit["events"]
                     if start_ts_ms <= event["funding_timestamp_ms"] < end_ts_ms]
    missing_intervals = []
    cadence_status = "NOT_REQUESTED"
    if expected_interval_ms is not None:
        observed = {event["funding_timestamp_ms"] for event in window_events}
        if expected_anchor_ts_ms is not None:
            # Anchor defines the expected UTC settlement grid. This checks the
            # complete requested window, including leading/trailing omissions.
            first_expected = start_ts_ms + (
                (expected_anchor_ts_ms - start_ts_ms) % expected_interval_ms
            )
            expected_count = (
                0 if first_expected >= end_ts_ms
                else ((end_ts_ms - 1 - first_expected) // expected_interval_ms) + 1
            )
            # Fail closed on malformed or excessively broad intervals rather
            # than allocating an unbounded expected-timestamp list.
            max_expected_events = 100_000
            if expected_count > max_expected_events:
                raise ValueError(
                    "expected cadence grid exceeds 100000 events; narrow the window "
                    "or use a larger expected_interval_ms"
                )
            expected = range(first_expected, end_ts_ms, expected_interval_ms)
            missing_intervals = [timestamp for timestamp in expected if timestamp not in observed]
            cadence_status = (
                "WINDOW_GAPS_FOUND" if missing_intervals
                else "ANCHORED_WINDOW_SEQUENCE_OK"
            ) if expected else "EMPTY_EXPECTED_WINDOW"
        elif not window_events:
            # Without an anchor, an empty window cannot establish cadence.
            cadence_status = "INSUFFICIENT_EVENTS"
        else:
            first = min(observed)
            last = max(observed)
            internal_expected_count = ((last - first) // expected_interval_ms) + 1
            max_expected_events = 100_000
            if internal_expected_count > max_expected_events:
                raise ValueError(
                    "expected cadence grid exceeds 100000 events; narrow the window "
                    "or use a larger expected_interval_ms"
                )
            cursor = first
            while cursor <= last:
                if cursor not in observed:
                    missing_intervals.append(cursor)
                cursor += expected_interval_ms
            cadence_status = (
                "INTERNAL_GAPS_FOUND" if missing_intervals
                else "INTERNAL_SEQUENCE_OK_BOUNDARIES_UNVERIFIED"
            )

    return {
        "symbol": symbol.strip().upper(),
        "events": audit["events"],
        "manifest": {
            "sha256": hashlib.sha256(raw_bytes).hexdigest(),
            "size_bytes": len(raw_bytes),
            "row_count": len(events),
            "requested_start_ts_ms": start_ts_ms,
            "requested_end_ts_ms": end_ts_ms,
            "first_event_ts_ms": audit["first_event_ts_ms"],
            "last_event_ts_ms": audit["last_event_ts_ms"],
            "in_window_event_count": audit["in_window_event_count"],
            "expected_interval_ms": expected_interval_ms,
            "expected_anchor_ts_ms": expected_anchor_ts_ms,
            "missing_expected_timestamps_ms": missing_intervals,
            "cadence_check_passed": (
                expected_interval_ms is not None
                and (bool(window_events) if expected_anchor_ts_ms is None else True)
                and cadence_status in ("ANCHORED_WINDOW_SEQUENCE_OK", "INTERNAL_SEQUENCE_OK_BOUNDARIES_UNVERIFIED")
                and not missing_intervals
            ),
            "cadence_check_status": cadence_status,
            "cadence_boundary_coverage_verified": expected_anchor_ts_ms is not None and cadence_status == "ANCHORED_WINDOW_SEQUENCE_OK",
            "source_verified": False,
            "coverage_verified": False,
            "notice": "SHA-256 identifies these exact CSV bytes; it does not authenticate exchange provenance or prove complete history.",
        },
        "research_only": True,
        "auto_trade_authorized": False,
    }
