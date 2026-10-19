"""Fail-closed orchestration for installing testnet protective exits.

This module is deliberately TESTNET-only. It installs the stop-loss first, then
the take-profit. It does not cancel sibling orders, manage fills, or enable live
trading; callers must reconcile the returned state before retrying.
"""
from decimal import Decimal, InvalidOperation


def ensure_testnet_protective_exits(
    adapter,
    symbol: str,
    stop_price,
    take_profit_price,
    client_order_prefix: str,
):
    """Ensure a visible reduce-only stop and target exist for the current position.

    The adapter must be a BinanceFuturesTestnet-compatible test double or client.
    A failed/ambiguous stop submission halts the workflow before target placement.
    Existing matching orders are reused; this function never blindly resubmits them.
    """
    if not isinstance(symbol, str) or not symbol.endswith("USDT") or not symbol[:-4].isalnum():
        return {"ok": False, "reason": "invalid_symbol"}
    if not isinstance(client_order_prefix, str) or not client_order_prefix or len(client_order_prefix) > 20:
        return {"ok": False, "reason": "invalid_client_order_prefix"}
    if not client_order_prefix.replace("-", "").replace("_", "").isalnum():
        return {"ok": False, "reason": "invalid_client_order_prefix"}
    try:
        stop = Decimal(str(stop_price))
        target = Decimal(str(take_profit_price))
        if not stop.is_finite() or not target.is_finite() or stop <= 0 or target <= 0:
            raise InvalidOperation
    except (InvalidOperation, TypeError, ValueError):
        return {"ok": False, "reason": "invalid_protective_prices"}

    snapshot = adapter.testnet_position_snapshot(symbol)
    if not snapshot.get("ok"):
        return {"ok": False, "reason": "position_snapshot_failed"}
    try:
        position = Decimal(str(snapshot["position_amount"]))
        entry = Decimal(str(snapshot["entry_price"]))
    except (InvalidOperation, TypeError, ValueError, KeyError):
        return {"ok": False, "reason": "invalid_position_snapshot"}
    if position == 0 or entry <= 0:
        return {"ok": False, "reason": "no_open_position"}

    is_long = position > 0
    if (is_long and not (stop < entry < target)) or (
        not is_long and not (target < entry < stop)
    ):
        return {"ok": False, "reason": "invalid_stop_target_geometry"}

    open_orders = snapshot.get("open_orders")
    if not isinstance(open_orders, list):
        return {"ok": False, "reason": "invalid_open_orders_snapshot"}
    protective = [
        order for order in open_orders
        if order.get("status") in {"NEW", "PARTIALLY_FILLED"}
        and order.get("reduce_only") is True
        and order.get("type") in {"STOP_MARKET", "TAKE_PROFIT_MARKET"}
    ]
    stops = [o for o in protective if o.get("type") == "STOP_MARKET"]
    targets = [o for o in protective if o.get("type") == "TAKE_PROFIT_MARKET"]
    if len(stops) > 1 or len(targets) > 1:
        return {"ok": False, "reason": "duplicate_protective_orders_require_manual_reconciliation"}
    if stops and Decimal(str(stops[0].get("stop_price", "0"))) != stop:
        return {"ok": False, "reason": "existing_stop_price_mismatch"}
    if targets and Decimal(str(targets[0].get("stop_price", "0"))) != target:
        return {"ok": False, "reason": "existing_target_price_mismatch"}

    quantity = abs(position)
    actions = []
    if not stops:
        result = adapter.submit_testnet_protective_exit(
            symbol, "stop_loss", quantity, stop, f"{client_order_prefix}-sl"
        )
        if not result.get("accepted"):
            return {"ok": False, "reason": "stop_submission_failed", "detail": result, "actions": actions}
        actions.append({"kind": "stop_loss", "result": result})
    if not targets:
        result = adapter.submit_testnet_protective_exit(
            symbol, "take_profit", quantity, target, f"{client_order_prefix}-tp"
        )
        if not result.get("accepted"):
            return {
                "ok": False,
                "reason": "take_profit_submission_failed_stop_remains",
                "detail": result,
                "actions": actions,
                "requires_reconciliation": True,
            }
        actions.append({"kind": "take_profit", "result": result})

    return {
        "ok": True,
        "mode": "testnet_protective_exits_requested",
        "symbol": symbol,
        "position_amount": str(position),
        "stop_price": str(stop),
        "take_profit_price": str(target),
        "actions": actions,
        "requires_post_submission_reconciliation": True,
    }
