"""Deterministic paper-only order lifecycle simulator; never connects to an exchange."""
from __future__ import annotations

from copy import deepcopy
from math import isfinite


def _positive_finite(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value <= 0:
        raise ValueError(f"{field} must be finite and positive")
    return float(value)


class PaperExecutionBook:
    """In-memory paper ledger with idempotent order creation and conservative OHLC exits."""

    def __init__(self):
        self.orders = {}

    def place_order(self, client_order_id, symbol, side, quantity, stop_loss, take_profit):
        if not isinstance(client_order_id, str) or not client_order_id.strip():
            raise ValueError("client_order_id is required")
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("symbol is required")
        side = str(side).upper()
        if side not in ("LONG", "SHORT"):
            raise ValueError("side must be LONG or SHORT")
        quantity = _positive_finite(quantity, "quantity")
        stop_loss = _positive_finite(stop_loss, "stop_loss")
        take_profit = _positive_finite(take_profit, "take_profit")
        if side == "LONG" and not stop_loss < take_profit:
            raise ValueError("LONG requires stop_loss below take_profit")
        if side == "SHORT" and not take_profit < stop_loss:
            raise ValueError("SHORT requires take_profit below stop_loss")

        payload = {
            "client_order_id": client_order_id,
            "symbol": symbol.strip().upper(),
            "side": side,
            "quantity": quantity,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
        }
        existing = self.orders.get(client_order_id)
        if existing is not None:
            original = {key: existing[key] for key in payload}
            if original != payload:
                raise ValueError("client_order_id reused with different order parameters")
            return deepcopy(existing)

        order = {
            **payload,
            "status": "OPEN",
            "filled_quantity": 0.0,
            "average_fill_price": None,
            "exit_price": None,
            "exit_reason": None,
        }
        self.orders[client_order_id] = order
        return deepcopy(order)

    def record_fill(self, client_order_id, quantity, price):
        order = self._get(client_order_id)
        quantity = _positive_finite(quantity, "fill quantity")
        price = _positive_finite(price, "fill price")
        if order["status"] not in ("OPEN", "PARTIALLY_FILLED"):
            raise ValueError("order is not fillable")
        remaining = order["quantity"] - order["filled_quantity"]
        if quantity > remaining + 1e-12:
            raise ValueError("fill quantity exceeds remaining order quantity")
        previous_qty = order["filled_quantity"]
        total_qty = previous_qty + quantity
        old_value = (order["average_fill_price"] or 0.0) * previous_qty
        order["average_fill_price"] = (old_value + price * quantity) / total_qty
        order["filled_quantity"] = total_qty
        order["status"] = "FILLED" if abs(total_qty - order["quantity"]) <= 1e-12 else "PARTIALLY_FILLED"
        return deepcopy(order)

    def cancel(self, client_order_id):
        order = self._get(client_order_id)
        if order["status"] not in ("OPEN", "PARTIALLY_FILLED"):
            raise ValueError("only open or partially filled orders can be cancelled")
        order["status"] = "CANCELLED"
        return deepcopy(order)

    def evaluate_protective_exit(self, client_order_id, candle):
        """Exit filled quantity on stop/target touch; stop wins if both are touched."""
        order = self._get(client_order_id)
        if order["status"] != "FILLED":
            raise ValueError("protective exits require a fully filled paper order")
        required = ("h", "l")
        if any(key not in candle for key in required):
            raise ValueError("candle requires high (h) and low (l)")
        high = _positive_finite(candle["h"], "candle high")
        low = _positive_finite(candle["l"], "candle low")
        if low > high:
            raise ValueError("candle low cannot exceed high")
        if order["side"] == "LONG":
            stop_hit = low <= order["stop_loss"]
            target_hit = high >= order["take_profit"]
        else:
            stop_hit = high >= order["stop_loss"]
            target_hit = low <= order["take_profit"]
        if not stop_hit and not target_hit:
            return deepcopy(order)
        if stop_hit:
            order["exit_price"] = order["stop_loss"]
            order["exit_reason"] = "STOP"
        else:
            order["exit_price"] = order["take_profit"]
            order["exit_reason"] = "TAKE_PROFIT"
        order["status"] = "CLOSED"
        return deepcopy(order)

    def get(self, client_order_id):
        return deepcopy(self._get(client_order_id))

    def _get(self, client_order_id):
        try:
            return self.orders[client_order_id]
        except KeyError as exc:
            raise KeyError(f"unknown client_order_id: {client_order_id}") from exc
