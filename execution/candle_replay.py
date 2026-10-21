"""Deterministic OHLC replay for a single paper position.

Consumes caller-supplied closed candles only. No exchange data is fetched and no
orders are sent. Candle timestamps are milliseconds and must be strictly
increasing and later than the signal's open timestamp.
"""
from __future__ import annotations

from execution.paper_trade import open_paper_trade, process_candle, trade_summary


def replay_paper_trade(
    event,
    candles,
    *,
    quantity,
    opened_at_ms,
    fee_rate=0.0005,
    mark_price=None,
):
    """Replay closed candles against one eligible notification-only event.

    Each candle is a dict with timestamp_ms, open, high, low, and close.
    Returns the mutated simulated trade, per-candle actions, and summary when
    available. An open position requires mark_price for a mark-to-market summary.
    """
    if not isinstance(candles, (list, tuple)):
        raise ValueError("candles_must_be_sequence")

    previous = opened_at_ms
    for candle in candles:
        if not isinstance(candle, dict):
            raise ValueError("invalid_candle")
        timestamp = candle.get("timestamp_ms")
        if (isinstance(timestamp, bool) or not isinstance(timestamp, int)
                or timestamp <= previous):
            raise ValueError("candles_must_be_strictly_increasing")
        previous = timestamp

    trade = open_paper_trade(
        event, quantity=quantity, fee_rate=fee_rate, opened_at_ms=opened_at_ms
    )
    actions = []
    for candle in candles:
        if trade["status"] != "OPEN":
            break
        payload = {key: candle.get(key) for key in ("open", "high", "low", "close")}
        result = process_candle(trade, payload, timestamp_ms=candle["timestamp_ms"])
        actions.append({
            "timestamp_ms": candle["timestamp_ms"],
            "status": result["status"],
            "action": result["action"],
            **({"exit_price": result["exit_price"]} if "exit_price" in result else {}),
        })

    summary = trade_summary(trade, mark_price=mark_price) if (
        trade["status"] == "CLOSED" or mark_price is not None
    ) else None
    return {
        "trade": trade,
        "actions": actions,
        "summary": summary,
        "research_only": True,
        "orders_sent": 0,
    }
