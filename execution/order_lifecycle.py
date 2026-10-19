"""Pure order lifecycle transition rules; no exchange I/O or order submission.

The reducer is intentionally conservative: unknown states, invalid quantities,
non-monotonic cumulative fills, and illegal transitions require reconciliation.
"""
from copy import deepcopy
import math


STATES = {
    "NEW",
    "ACKNOWLEDGED",
    "PARTIALLY_FILLED",
    "FILLED",
    "CANCELED",
    "REJECTED",
    "RECONCILIATION_REQUIRED",
}
TERMINAL = {"FILLED", "CANCELED", "REJECTED"}

# Exchange updates can arrive out of order. UNKNOWN/reconciliation states may
# resolve to any exchange-confirmed state, but never imply a new submission.
_ALLOWED = {
    "NEW": {"ACKNOWLEDGED", "PARTIALLY_FILLED", "FILLED", "CANCELED", "REJECTED",
            "RECONCILIATION_REQUIRED"},
    "ACKNOWLEDGED": {"PARTIALLY_FILLED", "FILLED", "CANCELED", "REJECTED",
                     "RECONCILIATION_REQUIRED"},
    "PARTIALLY_FILLED": {"PARTIALLY_FILLED", "FILLED", "CANCELED",
                         "RECONCILIATION_REQUIRED"},
    "FILLED": {"FILLED"},
    "CANCELED": {"CANCELED", "PARTIALLY_FILLED", "FILLED",
                 "RECONCILIATION_REQUIRED"},
    "REJECTED": {"REJECTED", "RECONCILIATION_REQUIRED"},
    "RECONCILIATION_REQUIRED": {"ACKNOWLEDGED", "PARTIALLY_FILLED", "FILLED",
                                "CANCELED", "REJECTED",
                                "RECONCILIATION_REQUIRED"},
}


def initial_order_state(client_order_id):
    """Create an unsubmitted local record; this function does not send orders."""
    if not isinstance(client_order_id, str) or not client_order_id.strip():
        raise ValueError("client_order_id_required")
    return {
        "client_order_id": client_order_id,
        "state": "NEW",
        "cumulative_filled_qty": 0.0,
        "seen_event_ids": [],
        "requires_reconciliation": False,
    }


def apply_order_update(record, update):
    """Apply one normalized exchange update and return a new record plus outcome.

    Required update fields: event_id, state, cumulative_filled_qty.
    Event IDs provide idempotency. Cumulative fill quantity must never decrease.
    Any malformed or contradictory update fails closed into reconciliation.
    """
    if not isinstance(record, dict) or record.get("state") not in STATES:
        raise ValueError("invalid_order_record")
    if not isinstance(update, dict):
        raise ValueError("invalid_order_update")

    result = deepcopy(record)
    seen = result.get("seen_event_ids")
    if not isinstance(seen, list):
        result["requires_reconciliation"] = True
        result["state"] = "RECONCILIATION_REQUIRED"
        return result, "invalid_event_ledger"

    event_id = update.get("event_id")
    if not isinstance(event_id, str) or not event_id.strip():
        result["requires_reconciliation"] = True
        result["state"] = "RECONCILIATION_REQUIRED"
        return result, "event_id_required"
    if event_id in seen:
        return result, "duplicate_event_ignored"

    new_state = update.get("state")
    quantity = update.get("cumulative_filled_qty")
    if (new_state not in STATES or isinstance(quantity, bool)
            or not isinstance(quantity, (int, float))
            or not math.isfinite(quantity) or quantity < 0):
        result["state"] = "RECONCILIATION_REQUIRED"
        result["requires_reconciliation"] = True
        result["seen_event_ids"].append(event_id)
        return result, "invalid_update_requires_reconciliation"

    previous_qty = result.get("cumulative_filled_qty", 0.0)
    if (isinstance(previous_qty, bool) or not isinstance(previous_qty, (int, float))
            or not math.isfinite(previous_qty) or previous_qty < 0
            or quantity < previous_qty):
        result["state"] = "RECONCILIATION_REQUIRED"
        result["requires_reconciliation"] = True
        result["seen_event_ids"].append(event_id)
        return result, "non_monotonic_fill_requires_reconciliation"

    current_state = result["state"]
    if new_state not in _ALLOWED[current_state]:
        result["state"] = "RECONCILIATION_REQUIRED"
        result["requires_reconciliation"] = True
        result["seen_event_ids"].append(event_id)
        return result, "illegal_transition_requires_reconciliation"

    # A FILLED exchange state must have a positive cumulative fill. A
    # cancellation/rejection may legitimately report zero or partial fill.
    if new_state == "FILLED" and quantity <= 0:
        result["state"] = "RECONCILIATION_REQUIRED"
        result["requires_reconciliation"] = True
        result["seen_event_ids"].append(event_id)
        return result, "filled_without_quantity_requires_reconciliation"

    result["state"] = new_state
    result["cumulative_filled_qty"] = float(quantity)
    result["seen_event_ids"].append(event_id)
    result["requires_reconciliation"] = new_state == "RECONCILIATION_REQUIRED"
    return result, "applied"
