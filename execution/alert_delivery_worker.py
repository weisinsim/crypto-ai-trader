"""Deliver queued entry alerts through Telegram, marking only confirmed sends.

This worker is deliberately separate from market scanning and order execution.
Failed deliveries remain PENDING for a later retry.
"""
from __future__ import annotations

from execution.telegram_notifier import send_telegram_alert


def deliver_pending(store, *, sender=send_telegram_alert, limit=20, now=None):
    """Attempt pending notifications and return safe delivery counters.

    The sender must return a receipt with delivered=True. Exceptions are
    counted by type only; error strings may contain sensitive provider data.
    """
    result = {"attempted": 0, "delivered": 0, "failed": 0, "errors": []}
    for item in store.pending(limit=limit):
        event_id = item.get("event_id")
        event = item.get("event")
        result["attempted"] += 1
        try:
            receipt = sender(event)
            if not isinstance(receipt, dict) or receipt.get("delivered") is not True:
                raise RuntimeError("delivery_not_confirmed")
            if store.mark_delivered(event_id, now=now):
                result["delivered"] += 1
            else:
                result["failed"] += 1
                result["errors"].append({"event_id": event_id, "reason": "outbox_mark_failed"})
        except Exception as exc:
            result["failed"] += 1
            result["errors"].append({"event_id": event_id, "reason": type(exc).__name__})
    return result
