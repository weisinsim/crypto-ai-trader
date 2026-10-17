"""Fail-closed portfolio exposure reconciliation for paper-ledger records only."""
from __future__ import annotations

from collections.abc import Mapping
from math import isfinite


_EXPOSURE_STATUSES = {"PARTIALLY_FILLED", "FILLED", "CANCELLED"}
_ALLOWED_STATUSES = {"OPEN", "PARTIALLY_FILLED", "FILLED", "CANCELLED", "CLOSED"}
_EXIT_REASONS = {"STOP", "TAKE_PROFIT"}


def _finite_number(value, field, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"invalid {field}")
    try:
        if not isfinite(value) or value < 0 or (positive and value <= 0):
            raise ValueError(f"invalid {field}")
        converted = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"invalid {field}") from exc
    if not isfinite(converted):
        raise ValueError(f"invalid {field}")
    return converted


def reconcile_paper_positions(orders):
    """Derive signed per-symbol exposure from a PaperExecutionBook.orders mapping.

    Cancelled orders retain exposure equal to their filled quantity; closed orders
    contribute zero. Raises ValueError rather than silently ignoring malformed data.
    This is a local paper helper, not exchange reconciliation.
    """
    if not isinstance(orders, Mapping):
        raise ValueError("orders must be a mapping")

    positions = {}
    for order_id, order in orders.items():
        if not isinstance(order_id, str) or not order_id.strip() or not isinstance(order, Mapping):
            raise ValueError("invalid order record")
        # Older lightweight callers may omit this field, but if present it must
        # agree with the ledger key or the position identity is ambiguous.
        if "client_order_id" in order and order["client_order_id"] != order_id:
            raise ValueError("order identity does not match ledger key")
        symbol = order.get("symbol")
        side = order.get("side")
        status = order.get("status")
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("invalid order symbol")
        if not isinstance(side, str) or side not in {"LONG", "SHORT"}:
            raise ValueError("invalid order side")
        if not isinstance(status, str) or status not in _ALLOWED_STATUSES:
            raise ValueError("invalid order status")

        # Real paper-ledger records always carry both protective levels. Keep
        # compatibility with older lightweight callers that omit both, but reject
        # partial or invalid protection metadata whenever either field is present.
        has_stop = "stop_loss" in order
        has_target = "take_profit" in order
        if has_stop != has_target:
            raise ValueError("protective levels must be provided together")
        if has_stop:
            stop_loss = _finite_number(order.get("stop_loss"), "stop_loss", positive=True)
            take_profit = _finite_number(order.get("take_profit"), "take_profit", positive=True)
            if side == "LONG" and not stop_loss < take_profit:
                raise ValueError("invalid LONG protective-level geometry")
            if side == "SHORT" and not take_profit < stop_loss:
                raise ValueError("invalid SHORT protective-level geometry")

        quantity = _finite_number(order.get("quantity"), "quantity", positive=True)
        filled = _finite_number(order.get("filled_quantity"), "filled_quantity")
        if filled > quantity + 1e-12:
            raise ValueError("inconsistent order quantities")

        if status == "OPEN" and filled != 0:
            raise ValueError("OPEN order cannot have filled exposure")
        if status == "PARTIALLY_FILLED" and not (0 < filled < quantity):
            raise ValueError("inconsistent partially filled order")
        if status == "FILLED" and abs(filled - quantity) > 1e-12:
            raise ValueError("inconsistent filled order")
        if status == "CANCELLED" and filled >= quantity - 1e-12:
            raise ValueError("cancelled order cannot be fully filled")
        if status == "CLOSED":
            if filled <= 0:
                raise ValueError("closed order must retain historical filled quantity")
            _finite_number(order.get("exit_price"), "exit_price", positive=True)
            if order.get("exit_reason") not in _EXIT_REASONS:
                raise ValueError("closed order has invalid exit reason")

        open_quantity = filled if status in _EXPOSURE_STATUSES else 0.0
        signed_quantity = open_quantity if side == "LONG" else -open_quantity
        key = symbol.strip().upper()
        bucket = positions.setdefault(key, {"net_quantity": 0.0, "open_quantity": 0.0, "legs": []})
        if open_quantity:
            net_quantity = bucket["net_quantity"] + signed_quantity
            gross_quantity = bucket["open_quantity"] + open_quantity
            if not isfinite(net_quantity) or not isfinite(gross_quantity):
                raise ValueError("aggregated exposure must remain finite")
            bucket["net_quantity"] = net_quantity
            bucket["open_quantity"] = gross_quantity
            bucket["legs"].append({
                "client_order_id": order_id,
                "side": side,
                "status": status,
                "quantity": open_quantity,
            })

    return positions
