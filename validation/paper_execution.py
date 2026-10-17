"""Deterministic paper-only order lifecycle simulator; never connects to an exchange."""
from __future__ import annotations

import json
import os
from copy import deepcopy
from math import isfinite
from pathlib import Path
from tempfile import NamedTemporaryFile


_SCHEMA_VERSION = 3


def _positive_finite(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be finite and positive")
    try:
        if not isfinite(value) or value <= 0:
            raise ValueError(f"{field} must be finite and positive")
        converted = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{field} must be finite and positive") from exc
    if not isfinite(converted) or converted <= 0:
        raise ValueError(f"{field} must be finite and positive")
    return converted


def _nonnegative_finite(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be finite and non-negative")
    try:
        if not isfinite(value) or value < 0:
            raise ValueError(f"{field} must be finite and non-negative")
        converted = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{field} must be finite and non-negative") from exc
    if not isfinite(converted):
        raise ValueError(f"{field} must be finite and non-negative")
    return converted


class PaperExecutionBook:
    """Paper ledger with optional atomic JSON persistence and conservative OHLC exits.

    Persistence is local-file durability for simulation only, not a distributed lock,
    exchange reconciliation mechanism, or production trading ledger.
    """

    def __init__(self, storage_path=None):
        self.storage_path = Path(storage_path) if storage_path is not None else None
        self.orders = {}
        if self.storage_path is not None and self.storage_path.exists():
            self._load()

    def _load(self):
        try:
            payload = json.loads(self.storage_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("paper ledger unreadable; refusing to start with empty state") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") not in (1, 2, _SCHEMA_VERSION):
            raise ValueError("unsupported or invalid paper ledger schema")
        source_schema = payload["schema_version"]
        orders = payload.get("orders")
        if not isinstance(orders, dict):
            raise ValueError("invalid paper ledger orders")
        required = {
            "client_order_id", "symbol", "side", "quantity", "stop_loss", "take_profit",
            "status", "filled_quantity", "average_fill_price", "exit_price", "exit_reason",
        }
        required_v2 = required | {"fill_events"}
        required_v3 = required_v2 | {"cancelled_remainder"}
        allowed_statuses = {"OPEN", "PARTIALLY_FILLED", "FILLED", "CANCELLED", "CLOSED"}
        restored = {}
        for key, order in orders.items():
            if not isinstance(key, str) or not isinstance(order, dict):
                raise ValueError("invalid order record in paper ledger")
            if source_schema == 1 and set(order) == required:
                order = {**order, "fill_events": {}}
            if source_schema in (1, 2) and set(order) == required_v2:
                order = {
                    **order,
                    "cancelled_remainder": (
                        order["status"] == "CANCELLED"
                        and 0 < order["filled_quantity"] < order["quantity"]
                    ),
                }
            elif source_schema in (1, 2) and set(order) != required_v2:
                raise ValueError("invalid order record in paper ledger")
            elif source_schema == 3 and set(order) != required_v3:
                raise ValueError("invalid order record in paper ledger")
            if not isinstance(order["fill_events"], dict):
                raise ValueError("invalid fill events in paper ledger")
            if not isinstance(order["cancelled_remainder"], bool):
                raise ValueError("invalid cancelled remainder flag in paper ledger")
            for fill_id, event in order["fill_events"].items():
                if not isinstance(fill_id, str) or not fill_id.strip() or not isinstance(event, dict) or set(event) != {"quantity", "price"}:
                    raise ValueError("invalid fill event record")
                _positive_finite(event["quantity"], "fill event quantity")
                _positive_finite(event["price"], "fill event price")
            if order["client_order_id"] != key or order["side"] not in {"LONG", "SHORT"}:
                raise ValueError("invalid order identity in paper ledger")
            if order["status"] not in allowed_statuses:
                raise ValueError("invalid order status in paper ledger")
            for field in ("quantity", "stop_loss", "take_profit"):
                _positive_finite(order[field], field)
            # Persisted state must preserve the same protective-level geometry
            # enforced when an order is first created.
            if order["side"] == "LONG" and not order["stop_loss"] < order["take_profit"]:
                raise ValueError("invalid LONG protective-level geometry in paper ledger")
            if order["side"] == "SHORT" and not order["take_profit"] < order["stop_loss"]:
                raise ValueError("invalid SHORT protective-level geometry in paper ledger")
            filled = _nonnegative_finite(order["filled_quantity"], "filled quantity")
            if filled > order["quantity"] + 1e-12:
                raise ValueError("invalid filled quantity in paper ledger")
            if filled > 0:
                _positive_finite(order["average_fill_price"], "average_fill_price")
            elif order["average_fill_price"] is not None:
                raise ValueError("invalid average fill price in paper ledger")
            for field in ("exit_price",):
                if order[field] is not None:
                    _positive_finite(order[field], field)
            status = order["status"]
            if status == "OPEN" and filled != 0:
                raise ValueError("OPEN order cannot have filled quantity")
            if status == "PARTIALLY_FILLED" and not (0 < filled < order["quantity"]):
                raise ValueError("invalid partially filled order quantity")
            if status == "FILLED" and abs(filled - order["quantity"]) > 1e-12:
                raise ValueError("invalid fully filled order quantity")
            if status == "CANCELLED" and filled >= order["quantity"] - 1e-12:
                raise ValueError("cancelled order cannot be fully filled")
            if status == "CLOSED" and (order["exit_price"] is None or order["exit_reason"] not in {"STOP", "TAKE_PROFIT"} or filled <= 0):
                raise ValueError("invalid closed order in paper ledger")
            has_cancelled_remainder = 0 < filled < order["quantity"] and order["status"] in {"CANCELLED", "CLOSED"}
            if order["cancelled_remainder"] != has_cancelled_remainder:
                raise ValueError("invalid cancelled remainder state in paper ledger")
            restored[key] = deepcopy(order)
        self.orders = restored

    def _save(self):
        if self.storage_path is None:
            return
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": _SCHEMA_VERSION, "orders": self.orders}
        temp_name = None
        try:
            with NamedTemporaryFile("w", encoding="utf-8", dir=self.storage_path.parent,
                                    prefix=f".{self.storage_path.name}.", suffix=".tmp",
                                    delete=False) as handle:
                temp_name = handle.name
                json.dump(payload, handle, sort_keys=True, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.storage_path)
        except (OSError, TypeError, ValueError):
            if temp_name:
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass
            raise

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
            **payload, "status": "OPEN", "filled_quantity": 0.0,
            "average_fill_price": None, "exit_price": None, "exit_reason": None,
            "fill_events": {}, "cancelled_remainder": False,
        }
        self.orders[client_order_id] = order
        try:
            self._save()
        except Exception:
            self.orders.pop(client_order_id, None)
            raise
        return deepcopy(order)

    def record_fill(self, client_order_id, quantity, price, fill_id=None):
        order = self._get(client_order_id)
        quantity = _positive_finite(quantity, "fill quantity")
        price = _positive_finite(price, "fill price")
        if fill_id is not None:
            if not isinstance(fill_id, str) or not fill_id.strip():
                raise ValueError("fill_id must be a non-empty string when provided")
            previous_event = order["fill_events"].get(fill_id)
            if previous_event is not None:
                if previous_event != {"quantity": quantity, "price": price}:
                    raise ValueError("fill_id reused with different fill parameters")
                return deepcopy(order)
        if order["status"] not in ("OPEN", "PARTIALLY_FILLED"):
            raise ValueError("order is not fillable")
        remaining = order["quantity"] - order["filled_quantity"]
        if quantity > remaining + 1e-12:
            raise ValueError("fill quantity exceeds remaining order quantity")
        before = deepcopy(order)
        previous_qty = order["filled_quantity"]
        total_qty = previous_qty + quantity
        if not isfinite(total_qty) or total_qty <= 0:
            raise ValueError("cumulative fill quantity must remain finite and positive")
        old_value = (order["average_fill_price"] or 0.0) * previous_qty
        new_value = price * quantity
        combined_value = old_value + new_value
        if not isfinite(old_value) or not isfinite(new_value) or not isfinite(combined_value):
            raise ValueError("cumulative fill value must remain finite")
        average_fill_price = combined_value / total_qty
        if not isfinite(average_fill_price) or average_fill_price <= 0:
            raise ValueError("average fill price must remain finite and positive")
        order["average_fill_price"] = average_fill_price
        order["filled_quantity"] = total_qty
        order["status"] = "FILLED" if abs(total_qty - order["quantity"]) <= 1e-12 else "PARTIALLY_FILLED"
        if fill_id is not None:
            order["fill_events"][fill_id] = {"quantity": quantity, "price": price}
        try:
            self._save()
        except Exception:
            self.orders[client_order_id] = before
            raise
        return deepcopy(order)

    def cancel(self, client_order_id):
        order = self._get(client_order_id)
        if order["status"] not in ("OPEN", "PARTIALLY_FILLED"):
            raise ValueError("only open or partially filled orders can be cancelled")
        before = deepcopy(order)
        order["status"] = "CANCELLED"
        order["cancelled_remainder"] = 0 < order["filled_quantity"] < order["quantity"]
        try:
            self._save()
        except Exception:
            self.orders[client_order_id] = before
            raise
        return deepcopy(order)

    def evaluate_protective_exit(self, client_order_id, candle):
        """Exit filled quantity on stop/target touch; stop wins if both are touched."""
        order = self._get(client_order_id)
        if order["status"] not in ("FILLED", "CANCELLED") or order["filled_quantity"] <= 0:
            raise ValueError("protective exits require filled paper exposure")
        if any(key not in candle for key in ("h", "l")):
            raise ValueError("candle requires high (h) and low (l)")
        high = _positive_finite(candle["h"], "candle high")
        low = _positive_finite(candle["l"], "candle low")
        if low > high:
            raise ValueError("candle low cannot exceed high")
        if order["side"] == "LONG":
            stop_hit, target_hit = low <= order["stop_loss"], high >= order["take_profit"]
        else:
            stop_hit, target_hit = high >= order["stop_loss"], low <= order["take_profit"]
        if not stop_hit and not target_hit:
            return deepcopy(order)
        before = deepcopy(order)
        if stop_hit:
            order["exit_price"], order["exit_reason"] = order["stop_loss"], "STOP"
        else:
            order["exit_price"], order["exit_reason"] = order["take_profit"], "TAKE_PROFIT"
        order["status"] = "CLOSED"
        try:
            self._save()
        except Exception:
            self.orders[client_order_id] = before
            raise
        return deepcopy(order)

    def get(self, client_order_id):
        return deepcopy(self._get(client_order_id))

    def _get(self, client_order_id):
        try:
            return self.orders[client_order_id]
        except KeyError as exc:
            raise KeyError(f"unknown client_order_id: {client_order_id}") from exc
