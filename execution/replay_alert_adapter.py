"""Adapter from historical replay rows to the entry-alert contract.

This is an offline validation bridge only: replay timestamps are candle-open
timestamps, and callers must supply the actual analysis time in Unix seconds.
It does not poll markets, send alerts, or authorize trades.
"""
from __future__ import annotations

from datetime import datetime, timedelta


def replay_row_to_snapshot(row, *, analysis_ts, model_status="RESEARCH", source="historical_replay"):
    """Translate one replay CSV/dict row into a normalized alert snapshot.

    Only explicit LONG/SHORT entries are eligible for candidate construction.
    WATCH and NO-TRADE rows are represented as non-actionable snapshots and
    will be rejected by build_entry_alert.
    """
    if not isinstance(row, dict):
        raise TypeError("row must be a dictionary")
    timestamp = row.get("timestamp")
    if not isinstance(timestamp, str) or not timestamp:
        raise ValueError("replay row requires an ISO-8601 timestamp")
    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("replay timestamp must include a timezone")
    # Replay decisions are made at the close of the hourly candle, not its open.
    candle_close_ms = int((parsed + timedelta(hours=1)).timestamp() * 1000)
    side = row.get("side")
    return {
        "symbol": row.get("model"),
        "confirmed_signal": side if side in {"LONG", "SHORT"} else None,
        "signal": side if side in {"LONG", "SHORT"} else None,
        "model_status": model_status,
        "confirmed_signal_time": candle_close_ms,
        "last_closed_candle": candle_close_ms,
        "analysis_ts": analysis_ts,
        "entry": row.get("entry"),
        "sl": row.get("stop"),
        "tp1": row.get("target"),
        "tp2": row.get("target2"),
        "confirmed_score": row.get("score"),
        "model_name": row.get("model", "unknown"),
        "model_version": row.get("model_version", "unknown"),
        "rr": row.get("rr"),
        "source": source,
    }
