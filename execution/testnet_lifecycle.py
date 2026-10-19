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
    """Install and then verify a reduce-only stop and target for a testnet position."""
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
    if not position.is_finite() or not entry.is_finite() or position == 0 or entry <= 0:
        return {"ok": False, "reason": "no_open_position_or_invalid_values"}

    is_long = position > 0
    if (is_long and not (stop < entry < target)) or (not is_long and not (target < entry < stop)):
        return {"ok": False, "reason": "invalid_stop_target_geometry"}
    expected_side = "SELL" if is_long else "BUY"

    open_orders = snapshot.get("open_orders")
    if not isinstance(open_orders, list) or any(not isinstance(order, dict) for order in open_orders):
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

    for order, expected_price, kind in (
        [(order, stop, "stop") for order in stops]
        + [(order, target, "target") for order in targets]
    ):
        try:
            order_price = Decimal(str(order.get("stop_price", "0")))
            order_quantity = Decimal(str(order.get("orig_quantity", "NaN")))
        except (InvalidOperation, TypeError, ValueError):
            return {"ok": False, "reason": f"existing_{kind}_order_invalid"}
        if order_price != expected_price:
            return {"ok": False, "reason": f"existing_{kind}_price_mismatch"}
        if order.get("side") != expected_side:
            return {"ok": False, "reason": f"existing_{kind}_side_mismatch"}
        if not order_quantity.is_finite() or order_quantity != abs(position):
            return {"ok": False, "reason": f"existing_{kind}_quantity_mismatch"}

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

    # Confirm the exchange state after requests; a successful HTTP response is not enough.
    final_snapshot = adapter.testnet_position_snapshot(symbol)
    if not final_snapshot.get("ok"):
        return {"ok": False, "reason": "post_submission_snapshot_failed", "actions": actions, "requires_reconciliation": True}
    try:
        final_position = Decimal(str(final_snapshot["position_amount"]))
        final_entry = Decimal(str(final_snapshot["entry_price"]))
    except (InvalidOperation, TypeError, ValueError, KeyError):
        return {"ok": False, "reason": "post_submission_position_invalid", "actions": actions, "requires_reconciliation": True}
    if not final_position.is_finite() or final_position != position or final_entry != entry:
        return {"ok": False, "reason": "position_changed_during_protection_setup", "actions": actions, "requires_reconciliation": True}
    final_orders = final_snapshot.get("open_orders")
    if not isinstance(final_orders, list) or any(not isinstance(order, dict) for order in final_orders):
        return {"ok": False, "reason": "post_submission_open_orders_invalid", "actions": actions, "requires_reconciliation": True}

    active_exits = [
        order for order in final_orders
        if order.get("status") in {"NEW", "PARTIALLY_FILLED"}
        and order.get("reduce_only") is True
        and order.get("type") in {"STOP_MARKET", "TAKE_PROFIT_MARKET"}
    ]
    verified = {}
    for order_type, expected_price in (("STOP_MARKET", stop), ("TAKE_PROFIT_MARKET", target)):
        matches = [order for order in active_exits if order.get("type") == order_type]
        if len(matches) != 1:
            return {"ok": False, "reason": f"post_submission_{order_type.lower()}_count_mismatch", "actions": actions, "requires_reconciliation": True}
        order = matches[0]
        try:
            actual_price = Decimal(str(order.get("stop_price", "0")))
            actual_quantity = Decimal(str(order.get("orig_quantity", "NaN")))
        except (InvalidOperation, TypeError, ValueError):
            return {"ok": False, "reason": "post_submission_order_invalid", "actions": actions, "requires_reconciliation": True}
        if (
            order.get("side") != expected_side
            or actual_price != expected_price
            or not actual_quantity.is_finite()
            or actual_quantity != abs(final_position)
        ):
            return {"ok": False, "reason": "post_submission_protective_order_mismatch", "actions": actions, "requires_reconciliation": True}
        verified[order_type] = {
            "side": order.get("side"),
            "quantity": str(actual_quantity),
            "trigger_price": str(actual_price),
            "client_order_id": order.get("client_order_id", ""),
        }

    return {
        "ok": True,
        "mode": "testnet_protective_exits_verified",
        "symbol": symbol,
        "position_amount": str(final_position),
        "stop_price": str(stop),
        "take_profit_price": str(target),
        "verified_orders": verified,
        "actions": actions,
        "requires_post_fill_sibling_cancellation": True,
    }
