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

    def test_rejects_non_json_numeric_payload(self):
        store = EntryAlertStore(":memory:")
        event = {"event_id": "abc", "eligible": True, "symbol": "BTCUSDT", "side": "LONG", "notification_only": True, "auto_trade_authorized": False, "bad": float("nan")}
        with self.assertRaises(ValueError):
            store.enqueue(event)


if __name__ == "__main__":
    unittest.main()
