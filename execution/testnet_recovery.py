"""Restart recovery primitives for TESTNET positions; never opens new exposure."""
from decimal import Decimal, InvalidOperation

from execution.testnet_lifecycle import ensure_testnet_protective_exits


def recover_testnet_position(adapter, symbol, stop_loss, take_profit, client_order_prefix):
    """Inspect exchange state and ensure protection for an existing position."""
    try:
        snapshot = adapter.testnet_position_snapshot(symbol)
    except Exception as exc:
        return {"ok": False, "reason": "recovery_snapshot_failed", "error_type": type(exc).__name__, "requires_manual_reconciliation": True}
    if not isinstance(snapshot, dict) or not snapshot.get("ok"):
        return {"ok": False, "reason": "recovery_snapshot_invalid", "snapshot": snapshot, "requires_manual_reconciliation": True}
    try:
        amount = Decimal(str(snapshot["position_amount"]))
        entry = Decimal(str(snapshot["entry_price"]))
        stop = Decimal(str(stop_loss))
        target = Decimal(str(take_profit))
        if not all(value.is_finite() for value in (amount, entry, stop, target)):
            raise InvalidOperation
    except (InvalidOperation, TypeError, ValueError, KeyError):
        return {"ok": False, "reason": "recovery_position_values_invalid", "requires_manual_reconciliation": True}
    open_orders = snapshot.get("open_orders")
    if not isinstance(open_orders, list) or any(not isinstance(order, dict) for order in open_orders):
        return {"ok": False, "reason": "recovery_open_orders_invalid", "requires_manual_reconciliation": True}
    active_orders = [order for order in open_orders if order.get("status") in {"NEW", "PARTIALLY_FILLED"}]
    if amount == 0:
        if active_orders:
            return {"ok": False, "reason": "recovery_flat_position_has_open_orders", "open_order_count": len(active_orders), "requires_manual_reconciliation": True}
        return {"ok": True, "mode": "testnet_recovery_no_open_position", "symbol": symbol, "entry_order_submitted": False}
    if entry <= 0:
        return {"ok": False, "reason": "recovery_entry_price_invalid", "position_amount": str(amount), "requires_manual_reconciliation": True}
    if (amount > 0 and not stop < entry < target) or (amount < 0 and not target < entry < stop):
        return {"ok": False, "reason": "recovery_protective_geometry_invalid", "position_amount": str(amount), "entry_price": str(entry), "requires_manual_reconciliation": True}
    try:
        protection = ensure_testnet_protective_exits(adapter, symbol, stop, target, client_order_prefix)
    except Exception as exc:
        return {"ok": False, "reason": "recovery_protection_check_failed", "error_type": type(exc).__name__, "position_amount": str(amount), "requires_manual_reconciliation": True}
    if not isinstance(protection, dict) or not protection.get("ok"):
        return {"ok": False, "reason": "recovery_protection_not_verified", "position_amount": str(amount), "protection": protection, "requires_manual_reconciliation": True}
    return {"ok": True, "mode": "testnet_position_recovered_with_verified_protection", "symbol": symbol, "position_amount": str(amount), "entry_price": str(entry), "protection": protection, "entry_order_submitted": False}
