"""Tests for pending alert delivery and retry behavior."""
import unittest

from execution.alert_delivery_worker import deliver_pending


class Store:
    def __init__(self, items):
        self.items = list(items)
        self.delivered = []

    def pending(self, limit=100):
        return self.items[:limit]

    def mark_delivered(self, event_id, now=None):
        self.delivered.append(event_id)
        self.items = [item for item in self.items if item["event_id"] != event_id]
        return True


class DeliveryWorkerTests(unittest.TestCase):
    def test_marks_only_confirmed_delivery(self):
        store = Store([
            {"event_id": "ok", "event": {"eligible": True}},
            {"event_id": "retry", "event": {"eligible": True}},
        ])

        def sender(event):
            if event is store.items[0]["event"]:
                return {"delivered": True}
            raise RuntimeError("sensitive provider detail")

        # Use event identity to make the outcome deterministic for each item.
        ok_event = store.items[0]["event"]
        result = deliver_pending(
            store,
            sender=lambda event: {"delivered": True} if event is ok_event else (_ for _ in ()).throw(RuntimeError("secret")),
            now=123,
        )
        self.assertEqual(result["delivered"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(store.delivered, ["ok"])
        self.assertEqual(store.items[0]["event_id"], "retry")
        self.assertNotIn("secret", str(result))

    def test_unconfirmed_receipt_stays_pending(self):
        store = Store([{"event_id": "pending", "event": {"eligible": True}}])
        result = deliver_pending(store, sender=lambda event: {"delivered": False})
        self.assertEqual(result["failed"], 1)
        self.assertEqual(store.delivered, [])
        self.assertEqual(len(store.items), 1)

    def test_limit_is_forwarded_to_store(self):
        class LimitedStore(Store):
            seen_limit = None
            def pending(self, limit=100):
                self.seen_limit = limit
                return []
        store = LimitedStore([])
        deliver_pending(store, sender=lambda event: {"delivered": True}, limit=7)
        self.assertEqual(store.seen_limit, 7)


if __name__ == "__main__":
    unittest.main()
