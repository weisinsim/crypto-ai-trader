"""Fail-closed comparison of expected positions with normalized testnet snapshots.

This is a reconciliation decision helper only. It does not submit, cancel, or modify
orders and cannot establish that the exchange snapshot itself is fresh or complete.
"""
from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
import time


def _decimal(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ValueError(f"invalid {field}")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"invalid {field}") from None
    if not number.is_finite():
        raise ValueError(f"invalid {field}")
    return number


def reconcile_testnet_state(
    expected_positions,
    snapshots,
    required_protective_order_ids=None,
    *,
    quantity_tolerance="0.00000001",
):
    """Compare expected signed quantities to per-symbol read-only testnet snapshots.

    expected_positions: {SYMBOL: signed quantity}; zero means expected flat.
    snapshots: {SYMBOL: adapter snapshot with ok, position_amount, open_orders}.
    required_protective_order_ids: {SYMBOL: iterable of required client order IDs}.

    Missing, malformed, failed, or mismatched state is rejected. Open protective
    orders are recognized by client_order_id/clientOrderId and reduce_only/reduceOnly.
    """
    if not isinstance(expected_positions, Mapping) or not expected_positions:
        return {"ok": False, "reasons": ["EXPECTED_POSITIONS_INVALID"], "symbols": {}}
    if not isinstance(snapshots, Mapping):
        return {"ok": False, "reasons": ["SNAPSHOTS_INVALID"], "symbols": {}}
    if required_protective_order_ids is None:
        required_protective_order_ids = {}
    if not isinstance(required_protective_order_ids, Mapping):
        return {"ok": False, "reasons": ["REQUIRED_PROTECTIVE_ORDERS_INVALID"], "symbols": {}}
    try:
        tolerance = _decimal(quantity_tolerance, "quantity_tolerance")
        if tolerance < 0:
            raise ValueError("invalid quantity_tolerance")
    except ValueError:
        return {"ok": False, "reasons": ["QUANTITY_TOLERANCE_INVALID"], "symbols": {}}

    reasons = []
    results = {}
    normalized_expected = {}
    for symbol, quantity in expected_positions.items():
        if not isinstance(symbol, str) or not symbol.strip():
            return {"ok": False, "reasons": ["EXPECTED_SYMBOL_INVALID"], "symbols": {}}
        try:
            normalized_expected[symbol.strip().upper()] = _decimal(quantity, "expected quantity")
        except ValueError:
            return {"ok": False, "reasons": ["EXPECTED_QUANTITY_INVALID"], "symbols": {}}
    if len(normalized_expected) != len(expected_positions):
        return {"ok": False, "reasons": ["DUPLICATE_NORMALIZED_SYMBOL"], "symbols": {}}

    for symbol, expected in normalized_expected.items():
        snapshot = snapshots.get(symbol)
        symbol_reasons = []
        if not isinstance(snapshot, Mapping) or snapshot.get("ok") is not True:
            symbol_reasons.append("SNAPSHOT_MISSING_OR_FAILED")
            results[symbol] = {"ok": False, "reasons": symbol_reasons}
            reasons.extend(f"{symbol}:{reason}" for reason in symbol_reasons)
            continue
        try:
            actual = _decimal(snapshot.get("position_amount"), "position_amount")
        except ValueError:
            symbol_reasons.append("POSITION_AMOUNT_INVALID")
            actual = None
        if actual is not None and abs(actual - expected) > tolerance:
            symbol_reasons.append("POSITION_QUANTITY_MISMATCH")

        orders = snapshot.get("open_orders")
        if not isinstance(orders, list):
            symbol_reasons.append("OPEN_ORDERS_INVALID")
            orders = []
        seen_ids = set()
        for order in orders:
            if not isinstance(order, Mapping):
                symbol_reasons.append("OPEN_ORDER_INVALID")
                continue
            order_id = order.get("client_order_id", order.get("clientOrderId"))
            if isinstance(order_id, str) and order_id:
                if order_id in seen_ids:
                    symbol_reasons.append("DUPLICATE_OPEN_ORDER_ID")
                seen_ids.add(order_id)
        required = required_protective_order_ids.get(symbol, [])
        if isinstance(required, (str, bytes)) or not isinstance(required, (list, tuple, set, frozenset)):
            symbol_reasons.append("REQUIRED_PROTECTIVE_ORDERS_INVALID")
            required = []
        valid_required_ids = {
            item for item in required if isinstance(item, str) and item.strip()
        }
        for order in orders:
            if not isinstance(order, Mapping):
                continue
            order_id = order.get("client_order_id", order.get("clientOrderId"))
            if order_id not in valid_required_ids:
                symbol_reasons.append("UNEXPECTED_OPEN_ORDER")
        for order_id in required:
            if not isinstance(order_id, str) or not order_id.strip():
                symbol_reasons.append("REQUIRED_PROTECTIVE_ORDER_ID_INVALID")
                continue
            matches = [
                order for order in orders
                if isinstance(order, Mapping)
                and order.get("client_order_id", order.get("clientOrderId")) == order_id
            ]
            if len(matches) != 1:
                symbol_reasons.append("PROTECTIVE_ORDER_MISSING_OR_AMBIGUOUS")
                continue
            order = matches[0]
            reduce_only = order.get("reduce_only", order.get("reduceOnly"))
            status = order.get("status")
            if reduce_only is not True:
                symbol_reasons.append("PROTECTIVE_ORDER_NOT_REDUCE_ONLY")
            if status not in {"NEW", "PARTIALLY_FILLED"}:
                symbol_reasons.append("PROTECTIVE_ORDER_NOT_OPEN")
            if order.get("type") not in {"STOP_MARKET", "TAKE_PROFIT_MARKET"}:
                symbol_reasons.append("PROTECTIVE_ORDER_TYPE_INVALID")
            expected_side = "SELL" if expected > 0 else "BUY" if expected < 0 else None
            if expected_side is None or order.get("side") != expected_side:
                symbol_reasons.append("PROTECTIVE_ORDER_SIDE_MISMATCH")
            try:
                order_quantity = _decimal(
                    order.get("orig_quantity", order.get("origQty")), "protective order quantity"
                )
                if order_quantity <= 0 or abs(order_quantity - abs(expected)) > tolerance:
                    symbol_reasons.append("PROTECTIVE_ORDER_QUANTITY_MISMATCH")
            except ValueError:
                symbol_reasons.append("PROTECTIVE_ORDER_QUANTITY_INVALID")
            try:
                trigger_price = _decimal(
                    order.get("stop_price", order.get("stopPrice")), "protective order trigger price"
                )
                if trigger_price <= 0:
                    symbol_reasons.append("PROTECTIVE_ORDER_TRIGGER_PRICE_INVALID")
            except ValueError:
                symbol_reasons.append("PROTECTIVE_ORDER_TRIGGER_PRICE_INVALID")
        results[symbol] = {
            "ok": not symbol_reasons,
            "expected_position_amount": str(expected),
            "actual_position_amount": str(actual) if actual is not None else None,
            "reasons": list(dict.fromkeys(symbol_reasons)),
        }
        reasons.extend(f"{symbol}:{reason}" for reason in symbol_reasons)

    unexpected_symbols = set(snapshots) - set(normalized_expected)
    for symbol in sorted(unexpected_symbols):
        snapshot = snapshots[symbol]
        if not isinstance(snapshot, Mapping) or snapshot.get("ok") is not True:
            reasons.append(f"{symbol}:UNEXPECTED_SYMBOL_SNAPSHOT_INVALID")
            continue
        try:
            amount = _decimal(snapshot.get("position_amount"), "position_amount")
        except ValueError:
            reasons.append(f"{symbol}:UNEXPECTED_SYMBOL_POSITION_INVALID")
            continue
        if amount != 0:
            reasons.append(f"{symbol}:UNEXPECTED_NONZERO_POSITION")
        orders = snapshot.get("open_orders")
        if not isinstance(orders, list):
            reasons.append(f"{symbol}:UNEXPECTED_OPEN_ORDERS_INVALID")
        elif orders:
            reasons.append(f"{symbol}:UNEXPECTED_OPEN_ORDERS")

    return {"ok": not reasons, "reasons": list(dict.fromkeys(reasons)), "symbols": results}


def validate_snapshot_timing(
    snapshot,
    *,
    now_ms,
    max_age_ms=2000,
    max_observation_window_ms=2000,
):
    """Validate local observation timing metadata; this cannot make reads atomic.

    The caller must pass the time at which it intends to act. Missing, malformed,
    stale, future-dated, or overly wide snapshots fail closed.
    """
    if not isinstance(snapshot, Mapping) or snapshot.get("ok") is not True:
        return {"ok": False, "reason": "SNAPSHOT_MISSING_OR_FAILED"}
    try:
        now = _decimal(now_ms, "now_ms")
        started = _decimal(snapshot.get("snapshot_started_at_ms"), "snapshot_started_at_ms")
        completed = _decimal(snapshot.get("snapshot_completed_at_ms"), "snapshot_completed_at_ms")
        duration = _decimal(snapshot.get("snapshot_duration_ms"), "snapshot_duration_ms")
        max_age = _decimal(max_age_ms, "max_age_ms")
        max_window = _decimal(max_observation_window_ms, "max_observation_window_ms")
    except ValueError:
        return {"ok": False, "reason": "SNAPSHOT_TIMING_INVALID"}
    if any(value < 0 for value in (now, started, completed, duration, max_age, max_window)):
        return {"ok": False, "reason": "SNAPSHOT_TIMING_INVALID"}
    if completed < started or duration > max_window or completed - started > max_window:
        return {"ok": False, "reason": "SNAPSHOT_OBSERVATION_WINDOW_TOO_WIDE"}
    if completed > now:
        return {"ok": False, "reason": "SNAPSHOT_TIMESTAMP_IN_FUTURE"}
    if now - completed > max_age:
        return {"ok": False, "reason": "SNAPSHOT_STALE"}
    return {
        "ok": True,
        "observed_age_ms": str(now - completed),
        "observation_window_ms": str(completed - started),
    }


def reconcile_restart_order_ledger(expected_client_order_ids, order_queries, snapshot, *, now_ms=None):
    """Read-only restart decision from known client IDs plus a fresh snapshot.

    This helper never submits or cancels orders. Any missing/ambiguous query or
    mismatch between queried active orders and the open-order snapshot blocks
    automatic recovery.
    """
    if (
        not isinstance(expected_client_order_ids, (list, tuple, set, frozenset))
        or isinstance(expected_client_order_ids, (str, bytes))
        or not expected_client_order_ids
        or any(not isinstance(item, str) or not item.strip() for item in expected_client_order_ids)
        or len(set(expected_client_order_ids)) != len(expected_client_order_ids)
    ):
        return {"ok": False, "reason": "EXPECTED_CLIENT_ORDER_IDS_INVALID"}
    if not isinstance(order_queries, Mapping):
        return {"ok": False, "reason": "ORDER_QUERIES_INVALID"}
    timing = validate_snapshot_timing(snapshot, now_ms=int(time.time() * 1000) if now_ms is None else now_ms)
    if not timing.get("ok"):
        return {"ok": False, "reason": "SNAPSHOT_TIMING_REJECTED", "detail": timing.get("reason")}
    orders = snapshot.get("open_orders")
    if not isinstance(orders, list) or any(not isinstance(order, Mapping) for order in orders):
        return {"ok": False, "reason": "OPEN_ORDERS_INVALID"}
    active_snapshot_ids = {
        order.get("client_order_id", order.get("clientOrderId"))
        for order in orders
        if order.get("status") in {"NEW", "PARTIALLY_FILLED"}
    }
    if any(not isinstance(item, str) or not item for item in active_snapshot_ids):
        return {"ok": False, "reason": "OPEN_ORDER_CLIENT_ID_INVALID"}
    decisions = {}
    for client_id in expected_client_order_ids:
        query = order_queries.get(client_id)
        if not isinstance(query, Mapping) or query.get("found") is not True:
            return {"ok": False, "reason": "ORDER_QUERY_MISSING_OR_AMBIGUOUS", "client_order_id": client_id}
        status = query.get("status")
        if status not in {"NEW", "PARTIALLY_FILLED", "FILLED", "CANCELED", "EXPIRED", "REJECTED"}:
            return {"ok": False, "reason": "ORDER_STATUS_UNKNOWN", "client_order_id": client_id}
        if query.get("client_order_id", client_id) != client_id:
            return {"ok": False, "reason": "ORDER_CLIENT_ID_MISMATCH", "client_order_id": client_id}
        is_active = status in {"NEW", "PARTIALLY_FILLED"}
        if is_active != (client_id in active_snapshot_ids):
            return {"ok": False, "reason": "ORDER_QUERY_SNAPSHOT_MISMATCH", "client_order_id": client_id}
        decisions[client_id] = {
            "status": status,
            "active": is_active,
            "exchange_order_id": str(query.get("exchange_order_id", "")),
        }
    untracked = active_snapshot_ids - set(expected_client_order_ids)
    if untracked:
        return {"ok": False, "reason": "UNTRACKED_ACTIVE_ORDER_ON_RESTART", "client_order_ids": sorted(untracked)}
    return {
        "ok": True,
        "mode": "read_only_restart_reconciliation",
        "automatic_order_submission_authorized": False,
        "orders": decisions,
    }
