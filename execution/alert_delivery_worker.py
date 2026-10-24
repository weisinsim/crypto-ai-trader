"""Provider-neutral notification delivery loop for the human-facing alert outbox.

This module never talks to an exchange or submits orders. A provider adapter is
injected by the caller; no real delivery is attempted unless one is supplied.
"""
from __future__ import annotations

import time


def _valid_ack(value):
    """Require an explicit success flag and a non-empty provider receipt ID."""
    return (
        isinstance(value, dict)
        and value.get("ok") is True
        and isinstance(value.get("receipt_id"), str)
        and bool(value["receipt_id"].strip())
    )


def deliver_pending_once(*, store, sender, limit=20, now=None):
    """Attempt each currently pending event once.

    sender(event_id, event) must return {'ok': True, 'receipt_id': '...'} only
    after validating the provider's success response. Exceptions and malformed
    acknowledgements leave the event pending for an operator-controlled retry.
    This deliberately does not implement automatic retries or claim handset
    receipt; those require provider-specific policy and evidence.
    """
    if not callable(sender):
        raise ValueError("sender must be a configured callable provider adapter")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit must be a positive integer")
    delivered = []
    failed = []
    for item in store.pending(limit=limit):
        event_id = item["event_id"]
        try:
            acknowledgement = sender(event_id, item["event"])
        except Exception as exc:
            failed.append({"event_id": event_id, "reason": type(exc).__name__})
            continue
        if not _valid_ack(acknowledgement):
            failed.append({"event_id": event_id, "reason": "invalid_provider_ack"})
            continue
        timestamp = time.time() if now is None else now
        if not store.mark_delivered(
            event_id, now=timestamp,
            provider_receipt_id=acknowledgement["receipt_id"].strip(),
        ):
            failed.append({"event_id": event_id, "reason": "outbox_transition_failed"})
            continue
        delivered.append({
            "event_id": event_id,
            "receipt_id": acknowledgement["receipt_id"].strip(),
        })
    return {
        "status": "OK" if not failed else ("DEGRADED" if delivered else "FAILED"),
        "attempted": len(delivered) + len(failed),
        "delivered": delivered,
        "failed": failed,
        "pending_remaining": len(store.pending(limit=limit)),
    }
