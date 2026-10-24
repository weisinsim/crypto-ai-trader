"""Tests for provider-neutral outbox delivery and fail-closed acknowledgements."""
import unittest

from execution.alert_delivery_worker import deliver_pending_once
from execution.alert_store import EntryAlertStore


def event(event_id):
    return {
        "event_id": event_id,
        "eligible": True,
        "symbol": "BTCUSDT",
        "side": "LONG",
        "notification_only": True,
        "auto_trade_authorized": False,
        "entry": 65000.0,
    }


class AlertDeliveryWorkerTests(unittest.TestCase):
    def setUp(self):
        self.store = EntryAlertStore(":memory:")
        self.store.enqueue(event("ok-1"), now=100)
        self.store.enqueue(event("bad-1"), now=100)
        self.store.enqueue(event("raise-1"), now=100)

    def test_only_explicit_provider_receipt_marks_delivered(self):
        def sender(event_id, payload):
            self.assertEqual(payload["event_id"], event_id)
            if event_id == "ok-1":
                return {"ok": True, "receipt_id": "provider-msg-123"}
            if event_id == "bad-1":
                return {"ok": True}  # No receipt: not sufficient evidence.
            raise TimeoutError("redacted")

        result = deliver_pending_once(store=self.store, sender=sender, now=101)
        self.assertEqual(result["status"], "DEGRADED")
        self.assertEqual(result["attempted"], 3)
        self.assertEqual(result["delivered"], [
            {"event_id": "ok-1", "receipt_id": "provider-msg-123"}
        ])
        self.assertEqual(
            {item["event_id"]: item["reason"] for item in result["failed"]},
            {"bad-1": "invalid_provider_ack", "raise-1": "TimeoutError"},
        )
        self.assertEqual(
            [item["event_id"] for item in self.store.pending()],
            ["bad-1", "raise-1"],
        )

    def test_malformed_ack_and_false_ack_leave_event_pending(self):
        for acknowledgement in (None, {}, {"ok": False, "receipt_id": "x"},
                                {"ok": True, "receipt_id": "  "}):
            store = EntryAlertStore(":memory:")
            store.enqueue(event("e-1"), now=100)
            result = deliver_pending_once(
                store=store, sender=lambda *_: acknowledgement, now=101
            )
            self.assertEqual(result["status"], "FAILED")
            self.assertEqual(len(store.pending()), 1)

    def test_missing_sender_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "configured callable"):
            deliver_pending_once(store=self.store, sender=None)

    def test_invalid_limit_rejected(self):
        with self.assertRaisesRegex(ValueError, "positive integer"):
            deliver_pending_once(store=self.store, sender=lambda *_: {"ok": True}, limit=0)


if __name__ == "__main__":
    unittest.main()
