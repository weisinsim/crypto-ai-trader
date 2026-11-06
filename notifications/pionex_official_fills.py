"""Pionex documented futures fills response normalization (offline).

Official GET /uapi/v1/trade/fills has startTime/endTime/limit (max 200),
not a documented cursor. Reaching the limit is NOT evidence of completeness.
No HTTP requests, notifications, or trading here.
"""
from decimal import Decimal, InvalidOperation


class PionexFillSchemaError(ValueError):
    pass


def validate_fills_window(response, *, symbol, start_ms, end_ms, limit=200):
    if (not isinstance(symbol, str) or not symbol.endswith("_USDT_PERP")
            or type(start_ms) is not int or type(end_ms) is not int
            or start_ms <= 0 or end_ms < start_ms
            or type(limit) is not int or not 1 <= limit <= 200):
        raise PionexFillSchemaError("Invalid request window")
    if (not isinstance(response, dict) or response.get("result") is not True
            or not isinstance(response.get("data"), dict)
            or not isinstance(response["data"].get("fills"), list)):
        raise PionexFillSchemaError("Invalid or unsuccessful fills response")
    raw = response["data"]["fills"]
    if len(raw) >= limit:
        raise PionexFillSchemaError("Possible truncated fill history; split window or halt")
    normalized = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
            raise PionexFillSchemaError("Invalid fill object")
        fid, oid = item.get("id"), item.get("orderId")
        ts = item.get("timestamp")
        if (not isinstance(fid, (str, int)) or isinstance(fid, bool)
                or not str(fid) or str(fid) in seen
                or not isinstance(oid, (str, int)) or isinstance(oid, bool)
                or not str(oid)
                or item.get("symbol") != symbol
                or item.get("side") not in ("BUY", "SELL")
                or type(ts) is not int or not start_ms <= ts <= end_ms):
            raise PionexFillSchemaError("Invalid fill identity, symbol or timestamp")
        try:
            qty = Decimal(str(item["size"]))
        except (KeyError, InvalidOperation, TypeError, ValueError):
            raise PionexFillSchemaError("Invalid fill size") from None
        if not qty.is_finite() or qty <= 0:
            raise PionexFillSchemaError("Invalid fill quantity")
        seen.add(str(fid))
        normalized.append({
            "fill_id": str(fid), "order_id": str(oid),
            "symbol": symbol, "side": item["side"],
            "quantity": str(qty), "timestamp_ms": ts,
        })
    return normalized
