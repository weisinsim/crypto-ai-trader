"""Deterministic identity helper for closed-candle score replay.

Research-only: this module does not calculate scores, fetch candles, persist events,
or place orders. Callers must pass the finalized candle identity and frozen score.
"""
from __future__ import annotations

import hashlib
import json
import math


def build_frozen_score_event(*, symbol, side, timeframe, candle_close_ts, model_version, score, score_breakdown=None):
    """Build a stable signal ID and normalized immutable event payload.

    The same logical signal (symbol, side, timeframe, closed-candle timestamp,
    model version) produces the same ID even if a replayed score drifts. This lets
    the audit compare score values under a stable event identity. Candle timestamps
    are milliseconds since epoch and must identify a *closed* candle.
    """
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("symbol must be a non-empty string")
    symbol = symbol.strip().upper()
    if not isinstance(side, str) or side.upper() not in {"LONG", "SHORT"}:
        raise ValueError("side must be LONG or SHORT")
    side = side.upper()
    if not isinstance(timeframe, str) or not timeframe.strip():
        raise ValueError("timeframe must be a non-empty string")
    timeframe = timeframe.strip().lower()
    if isinstance(candle_close_ts, bool) or not isinstance(candle_close_ts, int) or candle_close_ts < 0:
        raise ValueError("candle_close_ts must be a non-negative integer millisecond timestamp")
    if not isinstance(model_version, str) or not model_version.strip():
        raise ValueError("model_version must be a non-empty string")
    model_version = model_version.strip()
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
        raise ValueError("score must be a finite number")
    score = float(score)
    if not 0 <= score <= 100:
        raise ValueError("score must be in [0, 100]")

    if score_breakdown is None:
        score_breakdown = {}
    if not isinstance(score_breakdown, dict):
        raise ValueError("score_breakdown must be a mapping")
    try:
        # Canonicalize JSON-compatible values and reject NaN/Infinity.
        score_breakdown = json.loads(json.dumps(
            score_breakdown, sort_keys=True, separators=(",", ":"),
            ensure_ascii=True, allow_nan=False,
        ))
    except (TypeError, ValueError) as exc:
        raise ValueError("score_breakdown must contain finite JSON-compatible values") from exc

    identity = {
        "symbol": symbol,
        "side": side,
        "timeframe": timeframe,
        "candle_close_ts": candle_close_ts,
        "model_version": model_version,
    }
    # Keep identity independent of score so replay detects score drift for the same
    # candle/model event instead of reporting one missing and one unexpected event.
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    signal_id = "replay_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {
        "signal_id": signal_id,
        "symbol": symbol,
        "side": side,
        "signal_ts": candle_close_ts,
        "score": score,
        "score_breakdown": score_breakdown,
        "timeframe": timeframe,
        "candle_close_ts": candle_close_ts,
        "model_version": model_version,
        "research_only": True,
        "auto_trade_authorized": False,
    }


def compare_replayed_score_events(first, second):
    """Compare two frozen event payloads and report deterministic replay status.

    Only the canonical identity/output fields are compared. A mismatch is a
    research audit finding, never a trading signal or automatic model verdict.
    """
    fields = (
        "signal_id", "symbol", "side", "timeframe", "candle_close_ts",
        "signal_ts", "model_version", "score", "score_breakdown",
    )
    if not isinstance(first, dict) or not isinstance(second, dict):
        raise ValueError("both replay events must be mappings")
    differences = {
        field: {"first": first.get(field), "second": second.get(field)}
        for field in fields if first.get(field) != second.get(field)
    }
    return {
        "status": "DETERMINISTIC" if not differences else "MISMATCH",
        "compared_fields": list(fields),
        "differences": differences,
        "research_only": True,
        "auto_trade_authorized": False,
    }


def audit_replay_series(expected_events, replayed_events):
    """Compare two replay sequences by deterministic signal ID and report coverage.

    Inputs must be event mappings produced from the same declared replay window.
    Missing, extra, duplicate, or changed events are surfaced rather than silently
    dropped. This is a structural replay audit, not proof of model profitability.
    """
    if not isinstance(expected_events, (list, tuple)) or not isinstance(replayed_events, (list, tuple)):
        raise ValueError("expected_events and replayed_events must be sequences")

    def index_events(events, label):
        indexed = {}
        invalid = []
        for i, event in enumerate(events):
            if not isinstance(event, dict):
                invalid.append({"index": i, "reason": "event is not a mapping"})
                continue
            signal_id = event.get("signal_id")
            if not isinstance(signal_id, str) or not signal_id:
                invalid.append({"index": i, "reason": "missing signal_id"})
                continue
            if signal_id in indexed:
                invalid.append({"index": i, "reason": "duplicate signal_id", "signal_id": signal_id})
                continue
            indexed[signal_id] = event
        return indexed, invalid

    expected, expected_invalid = index_events(expected_events, "expected")
    replayed, replayed_invalid = index_events(replayed_events, "replayed")
    missing_ids = sorted(set(expected) - set(replayed))
    unexpected_ids = sorted(set(replayed) - set(expected))
    mismatches = []
    for signal_id in sorted(set(expected) & set(replayed)):
        comparison = compare_replayed_score_events(expected[signal_id], replayed[signal_id])
        if comparison["status"] != "DETERMINISTIC":
            mismatches.append({"signal_id": signal_id, "differences": comparison["differences"]})
    passed = not (expected_invalid or replayed_invalid or missing_ids or unexpected_ids or mismatches)
    return {
        "status": "PASS" if passed else "FAIL",
        "expected_event_count": len(expected_events),
        "replayed_event_count": len(replayed_events),
        "matched_event_count": len(set(expected) & set(replayed)),
        "missing_signal_ids": missing_ids,
        "unexpected_signal_ids": unexpected_ids,
        "mismatched_events": mismatches,
        "expected_input_errors": expected_invalid,
        "replayed_input_errors": replayed_invalid,
        "research_only": True,
        "auto_trade_authorized": False,
    }
