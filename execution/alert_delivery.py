"""One-pass worker to deliver pending entry alerts from the SQLite outbox.

Delivery is at-least-once: a network timeout after Telegram accepts a message
can leave the event pending and a later run may send it again. Event creation
is idempotent; external chat delivery cannot be made exactly-once by SQLite.
"""
from __future__ import annotations

from execution.telegram_notifier import NotificationDeliveryError, send_telegram_alert


def deliver_pending_alerts(store, *, limit=25, sender=send_telegram_alert):
    """Attempt each pending alert once and mark only confirmed deliveries."""
    delivered = []
    failed = []
    pending = store.pending(limit=limit)
    for item in pending:
        event_id = item["event_id"]
        try:
            receipt = sender(item["event"])
            if not isinstance(receipt, dict) or receipt.get("delivered") is not True:
                raise NotificationDeliveryError("sender_returned_no_delivery_receipt")
            if not store.mark_delivered(event_id):
                failed.append({"event_id": event_id, "reason": "outbox_status_update_failed"})
                continue
            delivered.append({"event_id": event_id, "receipt": receipt})
        except Exception as exc:
            # Never log the exception body: provider errors can include sensitive details.
            failed.append({
                "event_id": event_id,
                "reason": exc.args[0] if isinstance(exc, NotificationDeliveryError) and exc.args else "delivery_failed",
            })
    return {
        "attempted": len(pending),
        "delivered_count": len(delivered),
        "failed_count": len(failed),
        "delivered": delivered,
        "failed": failed,
        "delivery_semantics": "at_least_once",
    }
