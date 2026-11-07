import tempfile
import unittest
from pathlib import Path

from notifications.pionex_atomic_checkpoint import (
    AtomicCheckpointError, AtomicExitCheckpointStore,
)


def event():
    return {
        "event_id": "pionex-exit-" + "a" * 64,
        "source": "PIONEX_EXECUTION", "symbol": "BTC_USDT_PERP",
        "position_side": "LONG", "event_type": "PARTIAL_EXIT",
        "closed_quantity": "1", "remaining_quantity": "1",
        "fill_ids": ["f1"], "classification": "STOP_LOSS",
        "notification_ready": False,
    }


class AtomicCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "events.sqlite3"
        self.store = AtomicExitCheckpointStore(self.path)
        self.gates = dict(pages_complete=True, positions_verified=True,
                          fills_reconciled=True, order_evidence_verified=True)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_atomic_commit_and_restart(self):
        self.store.commit_verified_interval(
            "acct", "BTC_USDT_PERP", 100, event(), **self.gates)
        self.assertEqual(self.store.checkpoint("acct", "BTC_USDT_PERP"), 100)
        self.store.close()
        self.store = AtomicExitCheckpointStore(self.path)
        self.assertEqual(self.store.checkpoint("acct", "BTC_USDT_PERP"), 100)
        count = self.store._conn.execute(
            "SELECT count(*) FROM pionex_exit_staging").fetchone()[0]
        self.assertEqual(count, 1)

    def test_incomplete_evidence_never_writes(self):
        gates = dict(self.gates, fills_reconciled=False)
        with self.assertRaises(AtomicCheckpointError):
            self.store.commit_verified_interval(
                "acct", "BTC_USDT_PERP", 100, event(), **gates)
        self.assertIsNone(self.store.checkpoint("acct", "BTC_USDT_PERP"))

    def test_conflicting_event_rolls_back_watermark(self):
        self.store.commit_verified_interval(
            "acct", "BTC_USDT_PERP", 100, event(), **self.gates)
        changed = event()
        changed["closed_quantity"] = "2"
        with self.assertRaises(AtomicCheckpointError):
            self.store.commit_verified_interval(
                "acct", "BTC_USDT_PERP", 200, changed, **self.gates)
        self.assertEqual(self.store.checkpoint("acct", "BTC_USDT_PERP"), 100)

    def test_regression_rolls_back_new_event(self):
        self.store.commit_verified_interval(
            "acct", "BTC_USDT_PERP", 200, event(), **self.gates)
        changed = event()
        changed["event_id"] = "pionex-exit-" + "b" * 64
        with self.assertRaises(AtomicCheckpointError):
            self.store.commit_verified_interval(
                "acct", "BTC_USDT_PERP", 100, changed, **self.gates)
        count = self.store._conn.execute(
            "SELECT count(*) FROM pionex_exit_staging").fetchone()[0]
        self.assertEqual(count, 1)

    def test_rejects_malformed_event_id_without_checkpoint(self):
        bad = event()
        bad["event_id"] = "pionex-exit-" + "z" * 64
        with self.assertRaises(AtomicCheckpointError):
            self.store.commit_verified_interval(
                "acct", "BTC_USDT_PERP", 100, bad, **self.gates)
        self.assertIsNone(self.store.checkpoint("acct", "BTC_USDT_PERP"))

    def test_rejects_duplicate_fill_ids_without_checkpoint(self):
        bad = event()
        bad["fill_ids"] = ["f1", "f1"]
        with self.assertRaises(AtomicCheckpointError):
            self.store.commit_verified_interval(
                "acct", "BTC_USDT_PERP", 100, bad, **self.gates)
        self.assertIsNone(self.store.checkpoint("acct", "BTC_USDT_PERP"))

    def test_rejects_blank_fill_ids_without_checkpoint(self):
        bad = event()
        bad["fill_ids"] = ["  "]
        with self.assertRaises(AtomicCheckpointError):
            self.store.commit_verified_interval(
                "acct", "BTC_USDT_PERP", 100, bad, **self.gates)
        self.assertIsNone(self.store.checkpoint("acct", "BTC_USDT_PERP"))

    def test_verified_no_exit_can_advance(self):
        self.store.commit_verified_interval(
            "acct", "BTC_USDT_PERP", 100, None, **self.gates)
        self.assertEqual(self.store.checkpoint("acct", "BTC_USDT_PERP"), 100)


if __name__ == "__main__":
    unittest.main()
