"""Deterministic identity helper for closed-candle score replay.

Research-only: this module does not calculate scores, fetch candles, persist events,
or place orders. Callers must pass the finalized candle identity and frozen score.
"""
from __future__ import annotations

import hashlib
import json
import math


def build_frozen_score_event(*, symbol, side, timeframe, candle_close_ts, model_version, score):
    """Build a stable signal ID and normalized immutable event payload.

    The same canonical inputs produce the same ID. A changed model version, side,
    candle close time, or score produces a different ID. Candle timestamps are
    milliseconds since epoch and must identify a *closed* candle.
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

    identity = {
        "symbol": symbol,
        "side": side,
        "timeframe": timeframe,
        "candle_close_ts": candle_close_ts,
        "model_version": model_version,
        "score": score,
    }
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    signal_id = "replay_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {
        "signal_id": signal_id,
        "symbol": symbol,
        "side": side,
        "signal_ts": candle_close_ts,
        "score": score,
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
        "signal_ts", "model_version", "score",
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
