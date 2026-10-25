"""Fail-closed orchestration for installing testnet protective exits.

This module is deliberately TESTNET-only. It installs the stop-loss first, then
the take-profit. It does not cancel sibling orders, manage fills, or enable live
trading; callers must reconcile the returned state before retrying.
"""
from decimal import Decimal, InvalidOperation
import time

from validation.testnet_reconciliation import validate_snapshot_timing, reconcile_restart_order_ledger


def _ensure_testnet_protective_exits_impl(
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
    timing = validate_snapshot_timing(
        snapshot,
        now_ms=int(time.time() * 1000),
        max_age_ms=2000,
        max_observation_window_ms=2000,
    )
    if not timing.get("ok"):
        return {
            "ok": False,
            "reason": "position_snapshot_timing_rejected",
            "timing_reason": timing.get("reason"),
            "requires_reconciliation": True,
        }
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
    # The adapter snapshot is sourced from the exchange's openOrders endpoint.
    # Treat malformed/unknown records and any active order that is not a recognized
    # reduce-only protective exit as an ambiguity; never add protection on top of
    # an unresolved entry or unexpected order.
    for order in open_orders:
        if (
            not isinstance(order.get("status"), str)
            or order.get("status") not in {"NEW", "PARTIALLY_FILLED"}
            or not isinstance(order.get("type"), str)
            or not isinstance(order.get("side"), str)
            or (order.get("reduce_only") is not True and order.get("reduce_only") is not False)
        ):
            return {"ok": False, "reason": "unknown_or_malformed_open_order_requires_reconciliation"}
        if not (
            order.get("reduce_only") is True
            and order.get("type") in {"STOP_MARKET", "TAKE_PROFIT_MARKET"}
        ):
            return {"ok": False, "reason": "unexpected_active_order_requires_reconciliation"}
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
            return {
                "ok": False,
                "reason": "stop_submission_failed_requires_reconciliation",
                "detail": result,
                "actions": actions,
                "requires_reconciliation": True,
                "automatic_retry": False,
            }
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
    final_timing = validate_snapshot_timing(
        final_snapshot,
        now_ms=int(time.time() * 1000),
        max_age_ms=2000,
        max_observation_window_ms=2000,
    )
    if not final_timing.get("ok"):
        return {
            "ok": False,
            "reason": "post_submission_snapshot_timing_rejected",
            "timing_reason": final_timing.get("reason"),
            "actions": actions,
            "requires_reconciliation": True,
        }
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

    # Validate the entire post-submit snapshot, not just the two expected exits.
    # An unexpected entry/order appearing during setup makes the exchange state
    # ambiguous; do not report successful protection while ignoring it.
    for order in final_orders:
        if (
            not isinstance(order.get("status"), str)
            or order.get("status") not in {"NEW", "PARTIALLY_FILLED"}
            or not isinstance(order.get("type"), str)
            or not isinstance(order.get("side"), str)
            or (order.get("reduce_only") is not True and order.get("reduce_only") is not False)
        ):
            return {"ok": False, "reason": "post_submission_order_unknown_or_malformed", "actions": actions, "requires_reconciliation": True}
        if not (
            order.get("reduce_only") is True
            and order.get("type") in {"STOP_MARKET", "TAKE_PROFIT_MARKET"}
        ):
            return {"ok": False, "reason": "post_submission_unexpected_active_order", "actions": actions, "requires_reconciliation": True}

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



def _cancel_testnet_sibling_after_flatten_impl(
    adapter,
    symbol: str,
    filled_exit_client_order_id: str,
    sibling_exit_client_order_id: str,
):
    """Cancel the sibling only after the filled exit and flat position are confirmed.

    Partial fills never trigger cancellation because the remaining exposure still
    needs protection. Ambiguous reads/cancels fail closed for manual reconciliation.
    """
    for value in (filled_exit_client_order_id, sibling_exit_client_order_id):
        if not isinstance(value, str) or not value or len(value) > 36:
            return {"ok": False, "reason": "invalid_client_order_id"}
        if not value.replace("-", "").replace("_", "").isalnum():
            return {"ok": False, "reason": "invalid_client_order_id"}
    filled = adapter.query_testnet_order(symbol, filled_exit_client_order_id)
    if not filled.get("found"):
        return {"ok": False, "reason": "filled_exit_lookup_failed", "requires_reconciliation": True}
    if filled.get("status") != "FILLED":
        return {
            "ok": False,
            "reason": "exit_not_fully_filled",
            "status": filled.get("status"),
            "requires_reconciliation": True,
        }
    try:
        executed = Decimal(str(filled.get("executed_quantity", "0")))
        if not executed.is_finite() or executed <= 0:
            raise InvalidOperation
    except (InvalidOperation, TypeError, ValueError):
        return {"ok": False, "reason": "invalid_filled_quantity", "requires_reconciliation": True}

    snapshot = adapter.testnet_position_snapshot(symbol)
    if not snapshot.get("ok"):
        return {"ok": False, "reason": "position_snapshot_failed", "requires_reconciliation": True}
    try:
        position = Decimal(str(snapshot["position_amount"]))
    except (InvalidOperation, TypeError, ValueError, KeyError):
        return {"ok": False, "reason": "invalid_position_snapshot", "requires_reconciliation": True}
    if not position.is_finite() or position != 0:
        return {
            "ok": False,
            "reason": "position_not_flat_sibling_must_remain",
            "position_amount": str(position),
            "requires_reconciliation": True,
        }

    sibling = adapter.query_testnet_order(symbol, sibling_exit_client_order_id)
    if not sibling.get("found"):
        return {"ok": False, "reason": "sibling_lookup_failed", "requires_reconciliation": True}
    if sibling.get("status") in {"CANCELED", "EXPIRED", "REJECTED"}:
        return {"ok": True, "mode": "testnet_sibling_already_inactive", "sibling_status": sibling.get("status")}
    if sibling.get("status") == "FILLED":
        return {
            "ok": False,
            "reason": "both_exits_report_filled_check_exchange_history",
            "requires_reconciliation": True,
        }
    cancelled = adapter.cancel_testnet_order(symbol, sibling_exit_client_order_id)
    if not cancelled.get("cancelled"):
        return {
            "ok": False,
            "reason": "sibling_cancel_unconfirmed",
            "cancel_result": cancelled,
            "requires_reconciliation": True,
        }
    # A cancel acknowledgement is not proof the sibling is inactive: it may
    # have filled in the race between the pre-cancel lookup and cancellation.
    sibling_after_cancel = adapter.query_testnet_order(symbol, sibling_exit_client_order_id)
    if not sibling_after_cancel.get("found"):
        return {"ok": False, "reason": "post_cancel_sibling_lookup_failed", "requires_reconciliation": True}
    if sibling_after_cancel.get("status") not in {"CANCELED", "EXPIRED", "REJECTED"}:
        return {
            "ok": False,
            "reason": "post_cancel_sibling_not_confirmed_inactive",
            "sibling_status": sibling_after_cancel.get("status"),
            "requires_reconciliation": True,
        }

    final_snapshot = adapter.testnet_position_snapshot(symbol)
    if not final_snapshot.get("ok"):
        return {"ok": False, "reason": "post_cancel_snapshot_failed", "requires_reconciliation": True}
    try:
        final_position = Decimal(str(final_snapshot["position_amount"]))
    except (InvalidOperation, TypeError, ValueError, KeyError):
        return {"ok": False, "reason": "post_cancel_position_invalid", "requires_reconciliation": True}
    if final_position != 0:
        return {
            "ok": False,
            "reason": "position_reopened_or_not_flat_after_cancel",
            "position_amount": str(final_position),
            "requires_reconciliation": True,
        }
    return {
        "ok": True,
        "mode": "testnet_sibling_cancelled_after_flatten",
        "filled_exit_client_order_id": filled_exit_client_order_id,
        "sibling_exit_client_order_id": sibling_exit_client_order_id,
        "cancel_result": cancelled,
    }



def ensure_testnet_protective_exits(
    adapter,
    symbol: str,
    stop_price,
    take_profit_price,
    client_order_prefix: str,
):
    """Fail closed on adapter exceptions; never retry an ambiguous submission."""
    try:
        return _ensure_testnet_protective_exits_impl(
            adapter, symbol, stop_price, take_profit_price, client_order_prefix
        )
    except Exception as exc:
        return {
            "ok": False,
            "reason": "adapter_exception_requires_reconciliation",
            "error_type": type(exc).__name__,
            "requires_reconciliation": True,
            "automatic_retry": False,
        }


def cancel_testnet_sibling_after_flatten(
    adapter,
    symbol: str,
    filled_exit_client_order_id: str,
    sibling_exit_client_order_id: str,
):
    """Treat read/cancel exceptions as ambiguous; never retry a cancel blindly."""
    try:
        return _cancel_testnet_sibling_after_flatten_impl(
            adapter, symbol, filled_exit_client_order_id, sibling_exit_client_order_id
        )
    except Exception as exc:
        return {
            "ok": False,
            "reason": "adapter_exception_requires_reconciliation",
            "error_type": type(exc).__name__,
            "requires_reconciliation": True,
            "automatic_retry": False,
        }


def reconcile_testnet_restart(adapter, symbol: str, expected_client_order_ids):
    """Read-only restart recovery: query known IDs and compare with a fresh snapshot.

    This function never submits or cancels orders. Any exception, unknown order,
    stale snapshot, or disagreement requires manual reconciliation.
    """
    try:
        if not isinstance(symbol, str) or not symbol.endswith("USDT") or not symbol[:-4].isalnum():
            return {
                "ok": False,
                "reason": "restart_symbol_invalid",
                "requires_reconciliation": True,
                "automatic_retry": False,
            }
        if (
            isinstance(expected_client_order_ids, (str, bytes))
            or not isinstance(expected_client_order_ids, (list, tuple, set, frozenset))
            or not expected_client_order_ids
        ):
            return {
                "ok": False,
                "reason": "restart_client_order_ids_invalid",
                "requires_reconciliation": True,
                "automatic_retry": False,
            }
        snapshot = adapter.testnet_position_snapshot(symbol)
        if not isinstance(snapshot, dict) or not snapshot.get("ok"):
            return {"ok": False, "reason": "restart_snapshot_failed", "requires_reconciliation": True, "automatic_retry": False}
        snapshot_symbol = snapshot.get("symbol")
        if snapshot_symbol is not None and snapshot_symbol != symbol:
            return {
                "ok": False,
                "reason": "restart_snapshot_symbol_mismatch",
                "snapshot_symbol": snapshot_symbol,
                "requires_reconciliation": True,
                "automatic_retry": False,
            }
        timing = validate_snapshot_timing(
            snapshot,
            now_ms=int(time.time() * 1000),
            max_age_ms=2000,
            max_observation_window_ms=2000,
        )
        if not timing.get("ok"):
            return {
                "ok": False,
                "reason": "restart_snapshot_timing_rejected",
                "timing_reason": timing.get("reason"),
                "requires_reconciliation": True,
                "automatic_retry": False,
            }
        queries = {}
        for client_id in expected_client_order_ids:
            queries[client_id] = adapter.query_testnet_order(symbol, client_id)
        result = reconcile_restart_order_ledger(
            expected_client_order_ids,
            queries,
            snapshot,
            now_ms=int(time.time() * 1000),
        )
        if not result.get("ok"):
            return {**result, "requires_reconciliation": True, "automatic_retry": False}
        return {
            **result,
            "symbol": symbol,
            "position_amount": str(snapshot.get("position_amount")),
            "requires_reconciliation": False,
        }
    except Exception as exc:
        return {
            "ok": False,
            "reason": "restart_reconciliation_exception",
            "error_type": type(exc).__name__,
            "requires_reconciliation": True,
            "automatic_retry": False,
        }
