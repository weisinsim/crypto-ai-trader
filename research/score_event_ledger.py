"""Strict research-only linkage between frozen entry scores and realized trades.

This module does not create signals, fetch prices, persist records, or place orders.
Callers must supply the score captured at signal time and a realized net outcome.
"""
from __future__ import annotations

import math

VALID_SYMBOLS = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT", "XRPUSDT"}
VALID_SIDES = {"LONG", "SHORT"}


def _finite_number(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{field} must be a finite number")
    return float(value)


def validate_score_event(event):
    """Validate a frozen entry-time signal event and return a normalized copy."""
    if not isinstance(event, dict):
        raise ValueError("event must be a mapping")
    signal_id = event.get("signal_id")
    symbol = event.get("symbol")
    side = event.get("side")
    ts = event.get("signal_ts")
    score = _finite_number(event.get("score"), "score")
    if not isinstance(signal_id, str) or not signal_id.strip():
        raise ValueError("signal_id must be a non-empty stable identifier")
    if symbol not in VALID_SYMBOLS:
        raise ValueError("symbol is not in the supported research universe")
    if side not in VALID_SIDES:
        raise ValueError("side must be LONG or SHORT")
    if isinstance(ts, bool) or not isinstance(ts, int) or ts < 0:
        raise ValueError("signal_ts must be a non-negative integer millisecond timestamp")
    if not 0 <= score <= 100:
        raise ValueError("score must be in [0, 100]")
    return {"signal_id": signal_id.strip(), "symbol": symbol, "side": side,
            "signal_ts": ts, "score": score}


def link_realized_trades(score_events, realized_trades):
    """Join realized trade outcomes to frozen entry events by exact stable ID.

    Every supplied realized trade must match exactly one score event. Unmatched
    records and duplicate IDs fail closed instead of silently biasing calibration.
    Output rows are ready for the calibration helper but remain research-only.
    """
    events = {}
    for index, raw in enumerate(score_events):
        event = validate_score_event(raw)
        if event["signal_id"] in events:
            raise ValueError(f"duplicate signal_id in score events: {event['signal_id']}")
        events[event["signal_id"]] = event

    seen_trades = set()
    linked = []
    for index, trade in enumerate(realized_trades):
        if not isinstance(trade, dict):
            raise ValueError(f"trade {index} must be a mapping")
        trade_id = trade.get("signal_id")
        if not isinstance(trade_id, str) or not trade_id.strip():
            raise ValueError(f"trade {index} requires signal_id")
        trade_id = trade_id.strip()
        if trade_id in seen_trades:
            raise ValueError(f"duplicate signal_id in realized trades: {trade_id}")
        seen_trades.add(trade_id)
        event = events.get(trade_id)
        if event is None:
            raise ValueError(f"realized trade has no frozen score event: {trade_id}")
        if trade.get("symbol") != event["symbol"] or trade.get("side") != event["side"]:
            raise ValueError(f"symbol/side mismatch for signal_id: {trade_id}")
        exit_ts = trade.get("exit_ts")
        if isinstance(exit_ts, bool) or not isinstance(exit_ts, int) or exit_ts < event["signal_ts"]:
            raise ValueError(f"invalid exit_ts for signal_id: {trade_id}")
        net_return = _finite_number(trade.get("net_return"), "net_return")
        if net_return <= -1:
            raise ValueError(f"net_return must be greater than -1 for signal_id: {trade_id}")
        linked.append({"signal_id": trade_id, "symbol": event["symbol"],
                       "side": event["side"], "score": event["score"],
                       "signal_ts": event["signal_ts"], "exit_ts": exit_ts,
                       "net_return": net_return})
    return {"status": "RESEARCH_ONLY", "linked_trade_count": len(linked),
            "unlinked_score_event_count": len(set(events) - seen_trades),
            "trades": sorted(linked, key=lambda row: row["exit_ts"]),
            "auto_trade_authorized": False}
