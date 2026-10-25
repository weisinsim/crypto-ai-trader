"""Audit and apply supplied historical funding events to paper positions.

This module performs no network access and never places orders. Callers must
supply funding-event history from a trusted exchange source and separately
verify symbol, interval, pagination, and coverage. Event timestamps are settlement
instants in milliseconds UTC. An event is charged only when
entry_timestamp_ms <= event_timestamp_ms < exit_timestamp_ms.
"""
from __future__ import annotations

import math


def audit_funding_events(events, *, symbol, start_ts_ms, end_ts_ms):
    """Validate funding events and report whether supplied history covers a window.

    Internal consistency can be checked, but exchange history completeness
    cannot be proven from the supplied rows. coverage_verified is always False.
    """
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("symbol must be a non-empty string")
    for name, value in (("start_ts_ms", start_ts_ms), ("end_ts_ms", end_ts_ms)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a nonnegative integer timestamp")
    if end_ts_ms < start_ts_ms:
        raise ValueError("end_ts_ms must be >= start_ts_ms")
    if not isinstance(events, (list, tuple)):
        raise ValueError("events must be a list or tuple")

    normalized = []
    seen = set()
    previous_ts = None
    for i, event in enumerate(events):
        if not isinstance(event, dict):
            raise ValueError(f"event {i} must be a mapping")
        event_symbol = event.get("symbol")
        ts = event.get("funding_timestamp_ms")
        rate = event.get("funding_rate")
        if not isinstance(event_symbol, str) or event_symbol.strip().upper() != symbol.strip().upper():
            raise ValueError(f"event {i} symbol does not match requested symbol")
        if isinstance(ts, bool) or not isinstance(ts, int) or ts < 0:
            raise ValueError(f"event {i} funding_timestamp_ms must be a nonnegative integer")
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not math.isfinite(rate):
            raise ValueError(f"event {i} funding_rate must be finite")
        if ts in seen:
            raise ValueError(f"duplicate funding timestamp: {ts}")
        if previous_ts is not None and ts < previous_ts:
            raise ValueError("funding events must be sorted by timestamp")
        seen.add(ts)
        previous_ts = ts
        normalized.append({"symbol": event_symbol.strip().upper(),
                           "funding_timestamp_ms": ts, "funding_rate": float(rate)})

    in_window = [e for e in normalized if start_ts_ms <= e["funding_timestamp_ms"] < end_ts_ms]
    return {
        "symbol": symbol.strip().upper(),
        "window_start_ts_ms": start_ts_ms,
        "window_end_ts_ms": end_ts_ms,
        "supplied_event_count": len(normalized),
        "in_window_event_count": len(in_window),
        "first_event_ts_ms": normalized[0]["funding_timestamp_ms"] if normalized else None,
        "last_event_ts_ms": normalized[-1]["funding_timestamp_ms"] if normalized else None,
        "events": normalized,
        "coverage_verified": False,
        "source_verified": False,
        "warning": "Internal consistency only; event completeness and exchange provenance must be verified separately.",
    }


def calculate_position_funding(*, events, symbol, side, entry_timestamp_ms,
                               exit_timestamp_ms, entry_notional):
    """Calculate funding cashflow from supplied events while the position is open.

    Positive rates mean longs pay and shorts receive. Negative rates reverse
    the cashflow. funding_cost is signed: positive is a cost, negative a credit.
    Constant entry notional is an explicit approximation.
    """
    if side not in ("LONG", "SHORT"):
        raise ValueError("side must be LONG or SHORT")
    for name, value in (("entry_timestamp_ms", entry_timestamp_ms),
                        ("exit_timestamp_ms", exit_timestamp_ms)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a nonnegative integer timestamp")
    if exit_timestamp_ms < entry_timestamp_ms:
        raise ValueError("exit timestamp must be >= entry timestamp")
    if isinstance(entry_notional, bool) or not isinstance(entry_notional, (int, float)) or not math.isfinite(entry_notional) or entry_notional <= 0:
        raise ValueError("entry_notional must be finite and positive")
    audit = audit_funding_events(events, symbol=symbol,
                                 start_ts_ms=entry_timestamp_ms,
                                 end_ts_ms=exit_timestamp_ms)
    applied = [e for e in audit["events"]
               if entry_timestamp_ms <= e["funding_timestamp_ms"] < exit_timestamp_ms]
    side_sign = 1.0 if side == "LONG" else -1.0
    total_cost = sum(float(entry_notional) * e["funding_rate"] * side_sign for e in applied)
    return {
        "symbol": symbol.strip().upper(),
        "side": side,
        "entry_timestamp_ms": entry_timestamp_ms,
        "exit_timestamp_ms": exit_timestamp_ms,
        "entry_notional_assumption": float(entry_notional),
        "funding_event_count": len(applied),
        "applied_events": applied,
        "funding_cost": total_cost,
        "funding_history_verified": False,
        "notional_path_verified": False,
        "funding_method": "SUPPLIED_EVENT_TIMESTAMPS_ENTRY_INCLUSIVE_EXIT_EXCLUSIVE",
        "auto_trade_authorized": False,
        "warning": "Uses supplied events and constant entry notional; verify exchange history completeness and actual position notional before treating as realized funding.",
    }
