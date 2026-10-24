"""Regression tests for durable outbox restart and delivery invariants."""
import tempfile
import unittest
from pathlib import Path

from execution.alert_store import EntryAlertStore


def event(event_id="restart-1", **overrides):
    value = {
        "event_id": event_id,
        "eligible": True,
        "symbol": "BTCUSDT",
        "side": "LONG",
        "notification_only": True,
        "auto_trade_authorized": False,
        "entry": 65000.0,
    }
    value.update(overrides)
    return value


class AlertStoreRestartAndDeliveryTests(unittest.TestCase):
    def test_pending_event_survives_store_recreation_and_keeps_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "outbox.sqlite3"
            first = EntryAlertStore(path)
            original = event()
            self.assertTrue(first.enqueue(original, now=100))
            del first

            restarted = EntryAlertStore(path)
            pending = restarted.pending()
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]["event_id"], "restart-1")
            self.assertEqual(pending[0]["event"], original)
            self.assertEqual(pending[0]["created_at"], 100)

    def test_duplicate_after_restart_does_not_create_second_event(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "outbox.sqlite3"
            self.assertTrue(EntryAlertStore(path).enqueue(event(), now=100))
            restarted = EntryAlertStore(path)
            self.assertFalse(restarted.enqueue(event(), now=101))
            self.assertEqual(len(restarted.pending()), 1)

    def test_delivery_transition_is_terminal_for_pending_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "outbox.sqlite3"
            store = EntryAlertStore(path)
            self.assertTrue(store.enqueue(event(), now=100))
            self.assertTrue(store.mark_delivered("restart-1", now=101))
            restarted = EntryAlertStore(path)
            self.assertEqual(restarted.pending(), [])
            self.assertFalse(restarted.mark_delivered("restart-1", now=102))

    def test_delivery_cannot_be_recorded_before_creation(self):
        store = EntryAlertStore(":memory:")
        self.assertTrue(store.enqueue(event(), now=100))
        self.assertFalse(store.mark_delivered("restart-1", now=99))
        self.assertEqual(len(store.pending()), 1)

    def test_conflicting_reused_event_id_is_not_silently_overwritten(self):
        store = EntryAlertStore(":memory:")
        self.assertTrue(store.enqueue(event(), now=100))
        with self.assertRaisesRegex(ValueError, "entry_alert_event_id_payload_conflict"):
            store.enqueue(event(entry=65100.0), now=101)
        pending = store.pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["event"]["entry"], 65000.0)


if __name__ == "__main__":
    unittest.main()
