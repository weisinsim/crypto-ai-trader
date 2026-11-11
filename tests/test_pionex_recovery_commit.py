"""Offline tests of recovery/checkpoint boundary; no exchange access."""
import tempfile
import unittest
from pathlib import Path

from notifications.pionex_atomic_checkpoint import AtomicExitCheckpointStore
from notifications.pionex_recovery_commit import (
    RecoveryCommitError, commit_recovered_window,
)


class RecoveryCommitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = AtomicExitCheckpointStore(Path(self.tmp.name) / "checkpoint.db")
        self.gates = dict(pages_complete=True, positions_verified=True,
                          fills_reconciled=True, order_evidence_verified=True)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def commit(self, now, **kwargs):
        return commit_recovered_window(
            self.store, "acct", "BTC_USDT_PERP", now,
            overlap_ms=10, max_span_ms=50, **kwargs)

    def test_first_run_requires_verified_baseline(self):
        with self.assertRaisesRegex(RecoveryCommitError, "baseline"):
            self.commit(150, **self.gates)
        self.assertIsNone(self.store.checkpoint("acct", "BTC_USDT_PERP"))

    def test_verified_bounded_progress_without_skipping(self):
        self.store.commit_verified_interval("acct", "BTC_USDT_PERP", 100, **self.gates)
        first = self.commit(250, **self.gates)
        self.assertEqual(first["window"]["start_ms"], 90)
        self.assertEqual(first["window"]["end_ms"], 140)
        self.assertTrue(first["window"]["more_history_pending"])
        second = self.commit(250, **self.gates)
        self.assertEqual(second["window"]["start_ms"], 130)
        self.assertEqual(self.store.checkpoint("acct", "BTC_USDT_PERP"), 180)

    def test_missing_evidence_does_not_advance(self):
        self.store.commit_verified_interval("acct", "BTC_USDT_PERP", 100, **self.gates)
        with self.assertRaisesRegex(RecoveryCommitError, "incomplete"):
            self.commit(200, **dict(self.gates, fills_reconciled=False))
        self.assertEqual(self.store.checkpoint("acct", "BTC_USDT_PERP"), 100)

    def test_future_checkpoint_does_not_advance(self):
        self.store.commit_verified_interval("acct", "BTC_USDT_PERP", 200, **self.gates)
        with self.assertRaises(RecoveryCommitError):
            self.commit(100, **self.gates)
        self.assertEqual(self.store.checkpoint("acct", "BTC_USDT_PERP"), 200)

    def test_restart_resumes_from_durable_checkpoint(self):
        self.store.commit_verified_interval("acct", "BTC_USDT_PERP", 100, **self.gates)
        self.commit(250, **self.gates)
        self.store.close()
        self.store = AtomicExitCheckpointStore(Path(self.tmp.name) / "checkpoint.db")
        resumed = self.commit(250, **self.gates)
        self.assertEqual(resumed["window"]["start_ms"], 130)
        self.assertEqual(resumed["window"]["end_ms"], 180)

    def test_failed_poll_then_retry_does_not_skip_history(self):
        self.store.commit_verified_interval("acct", "BTC_USDT_PERP", 100, **self.gates)
        with self.assertRaises(RecoveryCommitError):
            self.commit(250, **dict(self.gates, pages_complete=False))
        self.assertEqual(self.store.checkpoint("acct", "BTC_USDT_PERP"), 100)
        recovered = self.commit(250, **self.gates)
        self.assertEqual(recovered["window"]["start_ms"], 90)
        self.assertEqual(recovered["window"]["end_ms"], 140)

    def test_bool_evidence_is_not_accepted(self):
        self.store.commit_verified_interval("acct", "BTC_USDT_PERP", 100, **self.gates)
        with self.assertRaises(RecoveryCommitError):
            self.commit(200, **dict(self.gates, pages_complete=1))
        self.assertEqual(self.store.checkpoint("acct", "BTC_USDT_PERP"), 100)


if __name__ == "__main__":
    unittest.main()
