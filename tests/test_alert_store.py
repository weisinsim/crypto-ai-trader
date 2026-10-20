"""Tests for durable idempotent entry-alert outbox."""
import tempfile
import unittest
from pathlib import Path

from execution.alert_store import EntryAlertStore


class EntryAlertStoreTests(unittest.TestCase):
    def test_enqueue_is_idempotent_and_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alerts.sqlite"
            store = EntryAlertStore(path)
            event = {"event_id": "abc", "eligible": True, "symbol": "BTCUSDT", "side": "LONG", "notification_only": True, "auto_trade_authorized": False}
            self.assertTrue(store.enqueue(event, now=10))
            self.assertFalse(store.enqueue(event, now=11))
            self.assertEqual(len(EntryAlertStore(path).pending()), 1)

    def test_same_event_id_with_conflicting_payload_fails_closed(self):
        store = EntryAlertStore(":memory:")
        event = {"event_id": "stable-id", "eligible": True, "symbol": "BTCUSDT",
                 "side": "LONG", "notification_only": True, "auto_trade_authorized": False,
                 "entry": 100.0}
        self.assertTrue(store.enqueue(event, now=10))
        changed = dict(event, entry=101.0)
        with self.assertRaisesRegex(ValueError, "entry_alert_event_id_payload_conflict"):
            store.enqueue(changed, now=11)
        pending = store.pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["event"]["entry"], 100.0)

    def test_ineligible_event_is_not_queued(self):
        store = EntryAlertStore(":memory:")
        event = {"event_id": "abc", "eligible": False, "symbol": "BTCUSDT", "side": "LONG", "notification_only": True, "auto_trade_authorized": False}
        self.assertFalse(store.enqueue(event))
        self.assertEqual(store.pending(), [])

    def test_requires_alert_contract_fields(self):
        store = EntryAlertStore(":memory:")
        event = {"event_id": "candidate", "eligible": True, "symbol": "BTCUSDT", "side": "LONG"}
        self.assertFalse(store.enqueue(event))
        event["notification_only"] = True
        event["auto_trade_authorized"] = True
        self.assertFalse(store.enqueue(event))
        event["auto_trade_authorized"] = False
        event["side"] = "WATCH-LONG"
        self.assertFalse(store.enqueue(event))
        self.assertEqual(store.pending(), [])

    def test_mark_delivered_only_once(self):
        store = EntryAlertStore(":memory:")
        event = {"event_id": "abc", "eligible": True, "symbol": "BTCUSDT", "side": "SHORT", "notification_only": True, "auto_trade_authorized": False}
        self.assertTrue(store.enqueue(event, now=10))
        self.assertTrue(store.mark_delivered("abc", now=11))
        self.assertFalse(store.mark_delivered("abc", now=12))
        self.assertEqual(store.pending(), [])

    def test_rejects_invalid_creation_timestamps(self):
        store = EntryAlertStore(":memory:")
        event = {"event_id": "timestamp-case", "eligible": True, "symbol": "BTCUSDT", "side": "LONG", "notification_only": True, "auto_trade_authorized": False}
        for stamp in (float("nan"), float("inf"), 0, -1, True, "bad"):
            self.assertFalse(store.enqueue(event, now=stamp))
        self.assertEqual(store.pending(), [])

    def test_rejects_invalid_delivery_timestamps(self):
        store = EntryAlertStore(":memory:")
        event = {"event_id": "delivery-time", "eligible": True, "symbol": "BTCUSDT", "side": "LONG", "notification_only": True, "auto_trade_authorized": False}
        self.assertTrue(store.enqueue(event, now=10))
        for stamp in (float("nan"), float("inf"), 0, -1, True, "bad"):
            self.assertFalse(store.mark_delivered("delivery-time", now=stamp))
        self.assertEqual(len(store.pending()), 1)

    def test_rejects_non_json_numeric_payload(self):
        store = EntryAlertStore(":memory:")
        event = {"event_id": "abc", "eligible": True, "symbol": "BTCUSDT", "side": "LONG", "notification_only": True, "auto_trade_authorized": False, "bad": float("nan")}
        self.assertFalse(store.enqueue(event))
        self.assertEqual(store.pending(), [])


if __name__ == "__main__":
    unittest.main()
