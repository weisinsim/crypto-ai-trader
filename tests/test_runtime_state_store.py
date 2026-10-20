import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from execution.runtime_guard import validate_runtime_state
from execution.runtime_state_store import RuntimeStateStore


class RuntimeStateStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tempdir.name) / "risk-state.sqlite3")
        self.store = RuntimeStateStore(self.db_path)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_in_memory_database_persists_across_store_operations(self):
        store = RuntimeStateStore(":memory:")
        now = datetime(2026, 10, 19, 10, tzinfo=timezone.utc)
        store.update_exchange_metrics(0, 0, 0, now_epoch=now.timestamp())
        store.record_realized_pnl(-1.5, now=now)
        snapshot = store.load_snapshot(now_epoch=now.timestamp())
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.daily_realized_pnl_usdt, -1.5)

    def test_missing_exchange_snapshot_fails_closed(self):
        self.assertIsNone(self.store.load_snapshot(now_epoch=1000))

    def test_metrics_pnl_and_controls_survive_reopen(self):
        now = datetime(2026, 10, 19, 10, tzinfo=timezone.utc)
        self.store.update_exchange_metrics(0, 0, 0, now_epoch=now.timestamp())
        self.store.record_realized_pnl(-2.5, now=now)
        self.store.record_execution_result(False)
        self.store.record_execution_result(False)
        self.store.set_emergency_stop(True)

        reopened = RuntimeStateStore(self.db_path)
        snapshot = reopened.load_snapshot(now_epoch=now.timestamp() + 2)
        self.assertEqual(snapshot.daily_realized_pnl_usdt, -2.5)
        self.assertEqual(snapshot.consecutive_execution_errors, 2)
        self.assertTrue(snapshot.emergency_stop)
        self.assertEqual(snapshot.snapshot_age_seconds, 2)
        self.assertFalse(validate_runtime_state(snapshot).allowed)

    def test_success_resets_consecutive_errors(self):
        self.store.record_execution_result(False)
        self.store.record_execution_result(False)
        self.store.record_execution_result(True)
        self.store.update_exchange_metrics(0, 0, 0, now_epoch=2000)
        snapshot = self.store.load_snapshot(now_epoch=2001)
        self.assertEqual(snapshot.consecutive_execution_errors, 0)

    def test_daily_pnl_is_scoped_to_utc_day(self):
        day_one = datetime(2026, 10, 19, 23, 30, tzinfo=timezone.utc)
        day_two = datetime(2026, 10, 20, 0, 30, tzinfo=timezone.utc)
        self.store.update_exchange_metrics(0, 0, 0, now_epoch=day_two.timestamp())
        self.store.record_realized_pnl(-4, now=day_one)
        self.store.record_realized_pnl(1, now=day_two)
        self.assertEqual(self.store.load_snapshot(now_epoch=day_two.timestamp()).daily_realized_pnl_usdt, 1)


    def test_realized_pnl_event_id_is_idempotent_across_restarts(self):
        now = datetime(2026, 10, 19, 10, tzinfo=timezone.utc)
        self.assertTrue(self.store.record_realized_pnl(-3.25, now=now, event_id="BTCUSDT-trade-100"))
        self.assertFalse(self.store.record_realized_pnl(-3.25, now=now, event_id="BTCUSDT-trade-100"))
        self.assertTrue(self.store.record_realized_pnl(1.25, now=now, event_id="BTCUSDT-trade-101"))
        reopened = RuntimeStateStore(self.db_path)
        self.assertFalse(reopened.record_realized_pnl(-3.25, now=now, event_id="BTCUSDT-trade-100"))
        reopened.update_exchange_metrics(0, 0, 0, now_epoch=now.timestamp())
        self.assertEqual(reopened.load_snapshot(now_epoch=now.timestamp()).daily_realized_pnl_usdt, -2.0)

    def test_realized_pnl_event_id_reuse_with_conflicting_payload_fails_closed(self):
        now = datetime(2026, 10, 19, 10, tzinfo=timezone.utc)
        self.assertTrue(self.store.record_realized_pnl(-3.25, now=now, event_id="trade-conflict"))
        with self.assertRaisesRegex(ValueError, "realized_pnl_event_id_payload_conflict"):
            self.store.record_realized_pnl(-8.75, now=now, event_id="trade-conflict")
        with self.assertRaisesRegex(ValueError, "realized_pnl_event_id_payload_conflict"):
            self.store.record_realized_pnl(-3.25, now=datetime(2026, 10, 20, 10, tzinfo=timezone.utc),
                                           event_id="trade-conflict")
        self.store.update_exchange_metrics(0, 0, 0, now_epoch=now.timestamp())
        self.assertEqual(self.store.load_snapshot(now_epoch=now.timestamp()).daily_realized_pnl_usdt, -3.25)

    def test_realized_pnl_event_id_must_be_valid(self):
        with self.assertRaises(ValueError):
            self.store.record_realized_pnl(1, event_id="  ")

    def test_invalid_metrics_rejected(self):
        with self.assertRaises(ValueError):
            self.store.update_exchange_metrics(0, float("nan"), 0, now_epoch=1000)
        with self.assertRaises(ValueError):
            self.store.record_realized_pnl(float("inf"))

    def test_corrupt_runtime_metrics_fail_closed(self):
        self.store.update_exchange_metrics(0, 0, 0, now_epoch=1000)
        import sqlite3
        with sqlite3.connect(self.db_path) as db:
            db.execute("UPDATE runtime_metrics SET open_positions=1.5 WHERE id=1")
        self.assertIsNone(self.store.load_snapshot(now_epoch=1001))

    def test_unreadable_runtime_database_fails_closed(self):
        broken = RuntimeStateStore(str(Path(self.tempdir.name) / "missing" / "risk.sqlite3"))
        # Replace its database file with invalid bytes after initialization.
        Path(broken.database_path).write_bytes(b"not a sqlite database")
        self.assertIsNone(broken.load_snapshot(now_epoch=1000))

    def test_future_dated_exchange_snapshot_fails_closed(self):
        self.store.update_exchange_metrics(0, 0, 0, now_epoch=2000)
        snapshot = self.store.load_snapshot(now_epoch=1000)
        self.assertEqual(snapshot.snapshot_age_seconds, -1000)
        decision = validate_runtime_state(snapshot, max_snapshot_age_seconds=30)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "runtime_snapshot_invalid")

    def test_stale_exchange_snapshot_is_detectable(self):
        self.store.update_exchange_metrics(0, 0, 0, now_epoch=1000)
        snapshot = self.store.load_snapshot(now_epoch=1100)
        self.assertFalse(validate_runtime_state(snapshot, max_snapshot_age_seconds=30).allowed)




    def test_entry_lock_is_exclusive_and_token_scoped(self):
        self.assertTrue(self.store.claim_entry_lock("lock-1"))
        self.assertFalse(self.store.claim_entry_lock("lock-2"))
        self.assertEqual(self.store.get_entry_lock()["token"], "lock-1")
        self.assertFalse(self.store.release_entry_lock("lock-2"))
        self.assertTrue(self.store.release_entry_lock("lock-1"))
        self.assertTrue(self.store.claim_entry_lock("lock-2"))

    def test_order_attempt_claim_is_atomic_and_survives_reopen(self):
        fingerprint = "a" * 64
        self.assertTrue(self.store.claim_order_attempt("order-1", fingerprint))
        self.assertFalse(self.store.claim_order_attempt("order-1", fingerprint))
        self.store.update_order_attempt("order-1", "RECONCILIATION_REQUIRED")
        self.assertEqual(self.store.get_order_attempt("order-1")["status"], "RECONCILIATION_REQUIRED")
        reopened = RuntimeStateStore(self.store.database_path)
        self.assertFalse(reopened.claim_order_attempt("order-1", fingerprint))

    def test_order_attempt_id_reuse_with_different_intent_fails_closed(self):
        self.assertTrue(self.store.claim_order_attempt("order-conflict", "d" * 64))
        with self.assertRaisesRegex(ValueError, "client_order_id_intent_fingerprint_conflict"):
            self.store.claim_order_attempt("order-conflict", "e" * 64)
        record = self.store.get_order_attempt("order-conflict")
        self.assertEqual(record["intent_fingerprint"], "d" * 64)
        self.assertEqual(record["status"], "CLAIMED")

    def test_order_attempt_rejects_invalid_fingerprint(self):
        with self.assertRaises(ValueError):
            self.store.claim_order_attempt("order-2", "not-a-sha256")


    def test_order_attempt_status_cannot_leave_reconciliation_required(self):
        fingerprint = "b" * 64
        self.assertTrue(self.store.claim_order_attempt("order-stuck", fingerprint))
        self.assertTrue(self.store.update_order_attempt("order-stuck", "RECONCILIATION_REQUIRED"))
        with self.assertRaisesRegex(ValueError, "invalid_order_attempt_status_transition"):
            self.store.update_order_attempt("order-stuck", "COMPLETED")
        with self.assertRaisesRegex(ValueError, "invalid_order_attempt_status_transition"):
            self.store.update_order_attempt("order-stuck", "CLAIMED")
        self.assertEqual(self.store.get_order_attempt("order-stuck")["status"], "RECONCILIATION_REQUIRED")

    def test_terminal_order_attempt_status_cannot_be_rewritten(self):
        fingerprint = "c" * 64
        self.assertTrue(self.store.claim_order_attempt("order-done", fingerprint))
        self.assertTrue(self.store.update_order_attempt("order-done", "COMPLETED"))
        with self.assertRaisesRegex(ValueError, "invalid_order_attempt_status_transition"):
            self.store.update_order_attempt("order-done", "RECONCILIATION_REQUIRED")
        self.assertEqual(self.store.get_order_attempt("order-done")["status"], "COMPLETED")

if __name__ == "__main__":
    unittest.main()
