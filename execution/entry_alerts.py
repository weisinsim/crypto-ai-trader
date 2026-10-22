"""Fail-closed, notification-ready entry alert contracts.

This module does not send messages or place orders. It converts a dashboard
snapshot into a deterministic, deduplicable candidate event. Research signals
may be surfaced as explicitly research-only alerts; NO-PRODUCTION models are
blocked. No alert authorizes automatic trading.
"""
from __future__ import annotations

import hashlib
import json
import math
import re

DIRECTIONS = {"LONG", "SHORT"}
ALERTABLE_MODEL_STATES = {"RESEARCH", "FROZEN", "PRODUCTION"}


def _finite_number(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def _finite_positive(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(number) and number > 0


def build_entry_alert(snapshot, *, now_ms, max_age_ms=90 * 60 * 1000):
    """Build a candidate alert from a fresh, closed-candle directional signal."""
    blockers = []
    if not _finite_number(now_ms) or float(now_ms) <= 0 or not _finite_number(max_age_ms) or float(max_age_ms) < 0:
        blockers.append("invalid_time_window")
        now_ms, max_age_ms = 0, 0
    if not isinstance(snapshot, dict):
        snapshot = {}
        blockers.append("invalid_snapshot")

    symbol = snapshot.get("symbol")
    side = snapshot.get("confirmed_signal")
    if not isinstance(side, str) or side not in DIRECTIONS:
        side = snapshot.get("signal")
    model_status = snapshot.get("model_status", "UNKNOWN")
    candle_ms = snapshot.get("confirmed_signal_time")
    if candle_ms is None:
        candle_ms = snapshot.get("last_closed_candle")
    analysis_ts = snapshot.get("analysis_ts")

    if not isinstance(symbol, str) or re.fullmatch(r"[A-Z0-9]{2,}USDT", symbol) is None:
        blockers.append("invalid_symbol")
    if not isinstance(side, str) or side not in DIRECTIONS:
        blockers.append("not_actionable_direction")
    if not isinstance(model_status, str) or model_status not in ALERTABLE_MODEL_STATES:
        blockers.append("model_not_approved_for_alerts")

    if not _finite_number(candle_ms):
        blockers.append("missing_closed_candle")
        candle_ms = None
    elif candle_ms > now_ms or now_ms - candle_ms > max_age_ms:
        blockers.append("stale_signal")

    if not _finite_number(analysis_ts):
        blockers.append("missing_analysis_timestamp")
    elif now_ms / 1000 - float(analysis_ts) > max_age_ms / 1000 or float(analysis_ts) > now_ms / 1000:
        blockers.append("stale_analysis")

    fields = {k: snapshot.get(k) for k in ("entry", "sl", "tp1", "tp2")}
    if not all(_finite_positive(v) for v in fields.values()):
        blockers.append("invalid_numeric_fields")
    elif side == "LONG" and not (fields["sl"] < fields["entry"] < fields["tp1"] <= fields["tp2"]):
        blockers.append("invalid_stop_geometry")
    elif side == "SHORT" and not (fields["tp2"] <= fields["tp1"] < fields["entry"] < fields["sl"]):
        blockers.append("invalid_stop_geometry")

    score = snapshot.get("confirmed_score", snapshot.get("score"))
    if not _finite_number(score) or not 0 <= score <= 100:
        blockers.append("invalid_score")

    payload = {
        "symbol": symbol,
        "side": side,
        "candle_ms": candle_ms,
        "model": snapshot.get("model_name", snapshot.get("model", "unknown")),
        "model_version": snapshot.get("model_version", "unknown"),
    }
    event_id = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    eligible = not blockers
    return {
        "event_id": event_id,
        "eligible": eligible,
        "blockers": sorted(set(blockers)),
        "symbol": symbol,
        "side": side if isinstance(side, str) and side in DIRECTIONS else "NO-TRADE",
        "model_status": model_status,
        "research_only": model_status != "PRODUCTION",
        "score": score if _finite_number(score) else None,
        "entry": fields["entry"] if _finite_positive(fields["entry"]) else None,
        "stop_loss": fields["sl"] if _finite_positive(fields["sl"]) else None,
        "take_profit_1": fields["tp1"] if _finite_positive(fields["tp1"]) else None,
        "take_profit_2": fields["tp2"] if _finite_positive(fields["tp2"]) else None,
        "risk_reward": snapshot.get("rr"),
        "signal_candle_ms": candle_ms,
        "analysis_ts": analysis_ts,
        "source": snapshot.get("source"),
        "notification_only": True,
        "auto_trade_authorized": False,
    }


def should_emit_alert(event, emitted_event_ids):
    """Return true only for an eligible event not already emitted by the caller."""
    return bool(
        isinstance(event, dict)
        and event.get("eligible") is True
        and isinstance(event.get("event_id"), str)
        and event["event_id"] not in emitted_event_ids
    )
