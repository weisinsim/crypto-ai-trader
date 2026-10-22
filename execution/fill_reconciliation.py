"""Normalize Binance USD-M Futures order/trade payloads for reconciliation.

Pure functions only: no network calls, no order submission, and no inference that
an order is safe to retry. Malformed or incomplete exchange data fails closed.
"""
from decimal import Decimal, InvalidOperation


def _decimal(value, field, *, allow_zero=True):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("invalid_" + field)
    if not result.is_finite() or result < 0 or (not allow_zero and result == 0):
        raise ValueError("invalid_" + field)
    return result


def normalize_order_payload(payload):
    """Normalize an exchange order response into a conservative stable shape."""
    if not isinstance(payload, dict):
        raise ValueError("order_payload_must_be_object")
    order_id = payload.get("orderId")
    client_id = payload.get("clientOrderId")
    status = payload.get("status")
    symbol = payload.get("symbol")
    if isinstance(order_id, bool) or not isinstance(order_id, (str, int)) or str(order_id) == "":
        raise ValueError("order_id_missing")
    if not isinstance(client_id, str) or not client_id.strip():
        raise ValueError("client_order_id_missing")
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("symbol_missing")
    status_map = {
        "NEW": "ACKNOWLEDGED",
        "PARTIALLY_FILLED": "PARTIALLY_FILLED",
        "FILLED": "FILLED",
        "CANCELED": "CANCELED",
        "EXPIRED": "CANCELED",
        "EXPIRED_IN_MATCH": "CANCELED",
        "REJECTED": "REJECTED",
    }
    if not isinstance(status, str) or status not in status_map:
        raise ValueError("unknown_order_status_requires_reconciliation")
    executed = _decimal(payload.get("executedQty"), "executed_qty")
    original = _decimal(payload.get("origQty"), "original_qty", allow_zero=False)
    if executed > original:
        raise ValueError("executed_qty_exceeds_original_qty")
    if status == "FILLED" and executed != original:
        raise ValueError("filled_quantity_mismatch")
    if status == "PARTIALLY_FILLED" and not (0 < executed < original):
        raise ValueError("partial_fill_quantity_mismatch")
    try:
        cumulative_filled_qty = float(executed)
    except (OverflowError, ValueError):
        raise ValueError("invalid_executed_qty")
    if cumulative_filled_qty == float("inf"):
        raise ValueError("invalid_executed_qty")
    return {
        "exchange_order_id": str(order_id),
        "client_order_id": client_id,
        "symbol": symbol,
        "status": status_map[status],
        "exchange_status": status,
        "executed_quantity": str(executed),
        "original_quantity": str(original),
        "cumulative_filled_qty": cumulative_filled_qty,
        "terminal": status in {"FILLED", "CANCELED", "REJECTED", "EXPIRED", "EXPIRED_IN_MATCH"},
        "reconciliation_required": False,
    }


def normalize_trade_payload(payload):
    """Normalize one Binance trade/fill with a stable, deduplicable event ID."""
    if not isinstance(payload, dict):
        raise ValueError("trade_payload_must_be_object")
    trade_id = payload.get("id", payload.get("tradeId"))
    order_id = payload.get("orderId")
    client_id = payload.get("clientOrderId")
    symbol = payload.get("symbol")
    if isinstance(trade_id, bool) or not isinstance(trade_id, (str, int)) or str(trade_id) == "":
        raise ValueError("trade_id_missing")
    if isinstance(order_id, bool) or not isinstance(order_id, (str, int)) or str(order_id) == "":
        raise ValueError("order_id_missing")
    if not isinstance(client_id, str) or not client_id.strip():
        raise ValueError("client_order_id_missing")
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("symbol_missing")
    qty = _decimal(payload.get("qty"), "trade_qty", allow_zero=False)
    price = _decimal(payload.get("price"), "trade_price", allow_zero=False)
    commission = _decimal(payload.get("commission", "0"), "commission")
    realized = payload.get("realizedPnl", "0")
    try:
        realized_decimal = Decimal(str(realized))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("invalid_realized_pnl")
    if not realized_decimal.is_finite():
        raise ValueError("invalid_realized_pnl")
    side = payload.get("side")
    if not isinstance(side, str) or side not in {"BUY", "SELL"}:
        raise ValueError("invalid_trade_side")
    timestamp_ms = payload.get("time")
    if (isinstance(timestamp_ms, bool) or not isinstance(timestamp_ms, int)
            or timestamp_ms < 0 or timestamp_ms > 253402300799999):
        raise ValueError("invalid_trade_timestamp")
    return {
        "event_id": f"binance-usdm:{symbol}:{order_id}:{trade_id}",
        "trade_id": str(trade_id),
        "exchange_order_id": str(order_id),
        "client_order_id": client_id,
        "symbol": symbol,
        "side": side,
        "quantity": str(qty),
        "price": str(price),
        "commission": str(commission),
        "realized_pnl": str(realized_decimal),
        "timestamp_ms": timestamp_ms,
    }
