"""Check funding event timestamps against an explicitly supplied settlement schedule.

This is an offline research audit, not an exchange schedule discovery service.
The caller must independently establish the schedule and history provenance.
"""
from __future__ import annotations

from research.funding_history_audit import audit_funding_events


def audit_funding_settlement_schedule(
    events, *, symbol, start_ts_ms, end_ts_ms, expected_settlement_ts_ms,
):
    """Fail closed on missing, unexpected, or duplicate scheduled settlements.

    All timestamps use UTC epoch milliseconds and the half-open window
    [start_ts_ms, end_ts_ms). A PASS establishes agreement with the caller's
    schedule only, never exchange completeness or authenticity.
    """
    audit = audit_funding_events(
        events, symbol=symbol, start_ts_ms=start_ts_ms, end_ts_ms=end_ts_ms,
    )
    if not isinstance(expected_settlement_ts_ms, (list, tuple)):
        raise ValueError("expected_settlement_ts_ms must be a list or tuple")
    expected = []
    for i, ts in enumerate(expected_settlement_ts_ms):
        if isinstance(ts, bool) or not isinstance(ts, int) or not start_ts_ms <= ts < end_ts_ms:
            raise ValueError(f"expected settlement {i} must be an integer within the requested window")
        if expected and ts <= expected[-1]:
            raise ValueError("expected settlements must be strictly increasing and unique")
        expected.append(ts)

    observed = [event["funding_timestamp_ms"] for event in audit["events"]]
    outside = [ts for ts in observed if not start_ts_ms <= ts < end_ts_ms]
    observed_set = set(observed)
    expected_set = set(expected)
    missing = [ts for ts in expected if ts not in observed_set]
    unexpected = [ts for ts in observed if ts not in expected_set]
    errors = []
    if outside:
        errors.append("events_outside_window")
    if missing:
        errors.append("missing_scheduled_settlements")
    if unexpected:
        errors.append("unexpected_settlement_timestamps")
    if not expected:
        errors.append("empty_expected_schedule_not_authoritative")
    return {
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "symbol": audit["symbol"],
        "window_start_ts_ms": start_ts_ms,
        "window_end_ts_ms": end_ts_ms,
        "expected_settlement_count": len(expected),
        "observed_settlement_count": len(observed),
        "missing_settlement_ts_ms": missing,
        "unexpected_settlement_ts_ms": unexpected,
        "outside_window_ts_ms": outside,
        "schedule_match": not errors,
        "exchange_schedule_verified": False,
        "exchange_history_verified": False,
        "auto_trade_authorized": False,
        "warning": "Schedule agreement is conditional on caller-supplied expected timestamps; verify venue schedule and API pagination independently.",
    }
