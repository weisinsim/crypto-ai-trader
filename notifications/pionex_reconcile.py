"""Fail-closed reconciliation of verified perpetual futures reductions.

Accepts normalized records only after exchange schema and pagination verification.
Never infers execution from market price; never sends orders or alerts.
"""
from decimal import Decimal, InvalidOperation
from hashlib import sha256


class ReconciliationError(ValueError):
    pass


def _quantity(value):
    if isinstance(value, bool) or value is None:
        raise ReconciliationError("Missing or invalid quantity")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ReconciliationError("Invalid quantity") from None
    if not number.is_finite() or number < 0:
        raise ReconciliationError("Quantity must be finite and nonnegative")
    return number


def _position(value):
    if not isinstance(value, dict) or set(value) != {"symbol", "side", "quantity"}:
        raise ReconciliationError("Invalid normalized position")
    symbol, side = value["symbol"], value["side"]
    if not isinstance(symbol, str) or not symbol.endswith("_USDT_PERP"):
        raise ReconciliationError("Unsupported futures symbol")
    if side not in ("LONG", "SHORT"):
        raise ReconciliationError("Unsupported position side")
    return symbol, side, _quantity(value["quantity"])


def reconcile_reduction(before, after, fills, *, coverage_complete=False, same_account=False):
    """Require matching account snapshots and complete, individually identified close fills.

    Fill effect CLOSE must be established from verified exchange order semantics.
    BUY/SELL direction alone never establishes a closing transaction.
    """
    if coverage_complete is not True or same_account is not True:
        raise ReconciliationError("Unverified account identity or incomplete fill coverage")
    symbol, side, old_qty = _position(before)
    after_symbol, after_side, new_qty = _position(after)
    if (symbol, side) != (after_symbol, after_side):
        raise ReconciliationError("Position identity mismatch")
    if new_qty >= old_qty:
        return None
    reduction = old_qty - new_qty
    if not isinstance(fills, list) or not fills:
        raise ReconciliationError("Missing executed fills")
    seen = set()
    total = Decimal(0)
    fill_ids = []
    for fill in fills:
        if not isinstance(fill, dict) or set(fill) != {
            "fill_id", "order_id", "symbol", "side", "position_side",
            "effect", "quantity", "timestamp_ms"
        }:
            raise ReconciliationError("Invalid normalized fill")
        fid, oid = fill["fill_id"], fill["order_id"]
        if not isinstance(fid, (str, int)) or isinstance(fid, bool) or not str(fid):
            raise ReconciliationError("Invalid fill identifier")
        if not isinstance(oid, (str, int)) or isinstance(oid, bool) or not str(oid):
            raise ReconciliationError("Invalid order identifier")
        if str(fid) in seen:
            raise ReconciliationError("Duplicate fill identifier")
        seen.add(str(fid))
        if (fill["symbol"] != symbol or fill["position_side"] != side
                or fill["effect"] != "CLOSE"
                or fill["side"] != ("SELL" if side == "LONG" else "BUY")):
            raise ReconciliationError("Fill does not prove a reduction")
        if type(fill["timestamp_ms"]) is not int or fill["timestamp_ms"] <= 0:
            raise ReconciliationError("Invalid fill timestamp")
        qty = _quantity(fill["quantity"])
        if qty <= 0:
            raise ReconciliationError("Zero-size fill")
        total += qty
        fill_ids.append(str(fid))
    if total != reduction:
        raise ReconciliationError("Executed fills do not reconcile with position delta")
    fingerprint = "|".join([symbol, side, *sorted(fill_ids)])
    return {
        "event_id": "pionex-exit-" + sha256(fingerprint.encode()).hexdigest(),
        "source": "PIONEX_EXECUTION",
        "symbol": symbol,
        "position_side": side,
        "event_type": "FULL_EXIT" if new_qty == 0 else "PARTIAL_EXIT",
        "closed_quantity": str(reduction),
        "remaining_quantity": str(new_qty),
        "fill_ids": sorted(fill_ids),
        "classification": "EXIT_REASON_UNVERIFIED",
        "notification_ready": False,  # No delivery until live schema/coverage and persistence gates exist.
    }
