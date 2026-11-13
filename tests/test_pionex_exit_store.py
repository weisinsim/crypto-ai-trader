"""Offline persistence tests; no exchange connection or notifications."""
import tempfile
import unittest
from pathlib import Path

from notifications.pionex_exit_store import ExitStoreError, PionexExitStore


def event():
    return {
        "event_id": "pionex-exit-abc", "source": "PIONEX_EXECUTION",
        "symbol": "BTC_USDT_PERP", "position_side": "LONG",
        "event_type": "PARTIAL_EXIT", "closed_quantity": "0.5",
        "remaining_quantity": "1", "fill_ids": ["fill-1"],
        "classification": "EXIT_REASON_UNVERIFIED", "notification_ready": False,
    }


class ExitStoreTests(unittest.TestCase):
    def test_restart_dedupe_and_no_delivery(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "exits.sqlite3"
            store = PionexExitStore(path)
            self.assertTrue(store.stage(event()))
            self.assertFalse(store.stage(event()))
            store.close()
            reopened = PionexExitStore(path)
            self.assertFalse(reopened.stage(event()))
            self.assertEqual(reopened.staged(), [event()])
            self.assertFalse(hasattr(reopened, "send"))
            self.assertFalse(hasattr(reopened, "place_order"))
            reopened.close()

    def test_conflicting_same_id_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            store = PionexExitStore(Path(folder) / "exits.sqlite3")
            self.assertTrue(store.stage(event()))
            conflicting = event()
            conflicting["closed_quantity"] = "0.9"
            with self.assertRaises(ExitStoreError):
                store.stage(conflicting)
            store.close()

    def test_delivery_ready_events_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            store = PionexExitStore(Path(folder) / "exits.sqlite3")
            unsafe = event()
            unsafe["notification_ready"] = True
            with self.assertRaises(ExitStoreError):
                store.stage(unsafe)
            store.close()

    def test_missing_persistence_directory_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ExitStoreError):
                PionexExitStore(Path(folder) / "missing" / "exits.sqlite3")


if __name__ == "__main__":
    unittest.main()
