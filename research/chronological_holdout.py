"""Deterministic chronological holdout partitioning for research trades.

Partitioning is by signal timestamp, not exit timestamp, so a trade is assigned
to the cohort where its decision originated. This utility cannot prove that a
holdout was never inspected or used for tuning; it only enforces a reproducible
temporal boundary and rejects ambiguous identifiers/timestamps.
"""
from __future__ import annotations

import math


def _timestamp(value, field):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer millisecond timestamp")
    return value


def partition_chronological_holdout(trades, *, cutoff_ts):
    """Split trade-like rows into development and holdout cohorts.

    Required fields: stable non-empty trade_id, signal_ts, exit_ts.
    Rows with signal_ts < cutoff_ts are development; signal_ts >= cutoff_ts are
    holdout. A trade may not exit before its own signal. Results are sorted by
    (signal_ts, trade_id) and the two cohorts are disjoint.
    """
    cutoff = _timestamp(cutoff_ts, "cutoff_ts")
    if not isinstance(trades, (list, tuple)):
        raise ValueError("trades must be a list or tuple")
    seen = set()
    development = []
    holdout = []
    for index, trade in enumerate(trades):
        if not isinstance(trade, dict):
            raise ValueError(f"trade {index} must be a mapping")
        trade_id = trade.get("trade_id")
        if not isinstance(trade_id, str) or not trade_id.strip():
            raise ValueError(f"trade {index} requires a non-empty trade_id")
        trade_id = trade_id.strip()
        if trade_id in seen:
            raise ValueError(f"duplicate trade_id: {trade_id}")
        seen.add(trade_id)
        signal_ts = _timestamp(trade.get("signal_ts"), f"{trade_id}.signal_ts")
        exit_ts = _timestamp(trade.get("exit_ts"), f"{trade_id}.exit_ts")
        if exit_ts < signal_ts:
            raise ValueError(f"trade {trade_id} exits before its signal")
        normalized = dict(trade)
        normalized.update({"trade_id": trade_id, "signal_ts": signal_ts, "exit_ts": exit_ts})
        (development if signal_ts < cutoff else holdout).append(normalized)

    key = lambda row: (row["signal_ts"], row["trade_id"])
    development.sort(key=key)
    holdout.sort(key=key)
    development_ids = {row["trade_id"] for row in development}
    holdout_ids = {row["trade_id"] for row in holdout}
    if development_ids & holdout_ids:
        raise AssertionError("internal partition overlap")
    return {
        "status": "RESEARCH_ONLY",
        "cutoff_ts": cutoff,
        "boundary_rule": "signal_ts < cutoff => development; signal_ts >= cutoff => holdout",
        "development_count": len(development),
        "holdout_count": len(holdout),
        "development": development,
        "holdout": holdout,
        "cohorts_disjoint": True,
        "chronological_order_verified": True,
        "holdout_unseen_or_untuned_verified": False,
        "warning": "This is a timestamp partition only. It cannot prove the holdout was not inspected, used for tuning, or contaminated by upstream feature leakage.",
        "auto_trade_authorized": False,
    }
