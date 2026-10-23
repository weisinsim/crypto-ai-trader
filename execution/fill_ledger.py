"""Idempotently apply normalized exchange fill events to the local PnL ledger.

This is a narrow integration layer. It does not fetch fills, infer missing events,
or authorize trading. Callers must supply exchange-confirmed trade payloads.
"""
from decimal import Decimal, InvalidOperation
import math

from execution.fill_reconciliation import normalize_trade_payload


def record_confirmed_fill_pnl(state_store, exchange_trade_payload):
    """Normalize one exchange trade and record realized PnL once per stable fill ID.

    Returns the normalized fill and whether its PnL event was newly inserted.
    Commission is exposed but deliberately not converted/subtracted: its asset
    may differ from USDT and requires an explicit FX/fee accounting policy.
    """
    if state_store is None or not callable(getattr(state_store, "record_realized_pnl", None)):
        raise ValueError("runtime_state_store_required")
    fill = normalize_trade_payload(exchange_trade_payload)
    try:
        realized_pnl = Decimal(fill["realized_pnl"])
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("invalid_normalized_realized_pnl")
    if not realized_pnl.is_finite():
        raise ValueError("invalid_normalized_realized_pnl")
    try:
        realized_pnl_float = float(realized_pnl)
    except (OverflowError, ValueError):
        raise ValueError("invalid_normalized_realized_pnl")
    if not math.isfinite(realized_pnl_float):
        raise ValueError("invalid_normalized_realized_pnl")
    inserted = state_store.record_realized_pnl(
        realized_pnl_float,
        event_id=fill["event_id"],
    )
    return {
        "ok": True,
        "event_id": fill["event_id"],
        "pnl_event_recorded": bool(inserted),
        "fill": fill,
        "commission_accounting": "not_converted_or_subtracted",
    }
