"""Tests for one-pass outbox delivery worker."""
import unittest

from execution.alert_store import EntryAlertStore
from execution.alert_delivery import deliver_pending_alerts


class AlertDeliveryWorkerTests(unittest.TestCase):
    def store_with_event(self):
        store = EntryAlertStore(":memory:")
        store.enqueue({
            "event_id": "evt-1", "eligible": True, "symbol": "BTCUSDT",
            "side": "LONG", "research_only": True,
        }, now=10)
        return store

    def test_confirmed_delivery_marks_event_delivered(self):
        store = self.store_with_event()
        result = deliver_pending_alerts(
            store, sender=lambda event: {"delivered": True, "message_id": 9}
        )
        self.assertEqual(result["delivered_count"], 1)
        self.assertEqual(store.pending(), [])

    def test_failed_delivery_remains_pending_for_retry(self):
        store = self.store_with_event()
        result = deliver_pending_alerts(
            store, sender=lambda event: (_ for _ in ()).throw(RuntimeError("network"))
        )
        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(len(store.pending()), 1)

    def test_no_pending_events_does_not_call_sender(self):
        store = EntryAlertStore(":memory:")
        result = deliver_pending_alerts(
            store, sender=lambda event: (_ for _ in ()).throw(AssertionError("should not call"))
        )
        self.assertEqual(result["attempted"], 0)
        self.assertEqual(result["delivered_count"], 0)

    def test_reports_at_least_once_delivery_semantics(self):
        store = self.store_with_event()
        result = deliver_pending_alerts(store, sender=lambda event: {"delivered": True})
        self.assertEqual(result["delivery_semantics"], "at_least_once")


if __name__ == "__main__":
    unittest.main()
