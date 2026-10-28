"""Regression tests for durable SQLite risk reservations."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest

from execution.risk_ledger import RiskLedgerError, SQLiteRiskLedger


class SQLiteRiskLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "risk-ledger.sqlite3")
        self.ledger = SQLiteRiskLedger(self.db_path)
        self.ledger.set_kill_switch(False, reason="unit test setup")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_kill_switch_defaults_to_active(self):
        fresh_path = str(Path(self.temp_dir.name) / "fresh-risk-ledger.sqlite3")
        fresh = SQLiteRiskLedger(fresh_path)
        status = fresh.kill_switch_status()
        self.assertTrue(status["active"])
        self.assertEqual(status["reason"], "default_fail_closed")

    def test_active_kill_switch_blocks_reservation_atomically(self):
        self.ledger.set_kill_switch(True, reason="test emergency stop")
        result = self.ledger.reserve("blocked-by-switch", "10", "0", "100")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "operator_kill_switch_active_or_unavailable")
        self.assertIsNone(self.ledger.get("blocked-by-switch"))

    def test_kill_switch_requires_boolean_and_a_reason(self):
        with self.assertRaises(RiskLedgerError):
            self.ledger.set_kill_switch("false", reason="not a bool")
        with self.assertRaises(RiskLedgerError):
            self.ledger.set_kill_switch(False, reason=" ")
        self.assertFalse(self.ledger.kill_switch_status()["active"])

    def test_kill_switch_persists_across_ledger_reconstruction(self):
        self.ledger.set_kill_switch(False, reason="operator verified testnet controls")
        reopened = SQLiteRiskLedger(self.db_path)
        status = reopened.kill_switch_status()
        self.assertFalse(status["active"])
        self.assertEqual(status["reason"], "operator verified testnet controls")
        reopened.set_kill_switch(True, reason="emergency stop")
        self.assertTrue(self.ledger.kill_switch_status()["active"])

    def test_kill_switch_activated_after_reservation_blocks_submission_transition(self):
        reservation = self.ledger.reserve("switch-race-1", "20", "0", "100")
        self.assertTrue(reservation["ok"])
        self.ledger.set_kill_switch(True, reason="operator stopped entries before POST")

        transition = self.ledger.transition(
            "switch-race-1", "SUBMITTING",
            detail="attempted submission transition after switch activation",
        )
        self.assertFalse(transition["ok"])
        self.assertEqual(
            transition["reason"], "operator_kill_switch_active_before_submission"
        )
        # Keep the reservation until reconciliation; do not silently free risk.
        self.assertEqual(self.ledger.get("switch-race-1")["state"], "RESERVED")
        self.assertEqual(self.ledger.active_reserved_notional(), "20")

    def test_reservation_counts_toward_next_account_cap_check(self):
        first = self.ledger.reserve("order-1", "60", "0", "100")
        second = self.ledger.reserve("order-2", "50", "0", "100")
        self.assertTrue(first["ok"])
        self.assertEqual(first["state"], "RESERVED")
        self.assertFalse(second["ok"])
        self.assertEqual(second["reason"], "account_notional_limit_exceeded")
        self.assertEqual(self.ledger.active_reserved_notional(), "60")

    def test_concurrent_reservations_cannot_both_exceed_cap(self):
        other = SQLiteRiskLedger(self.db_path)

        def reserve(ledger, order_id):
            return ledger.reserve(order_id, "60", "0", "100")

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(
                lambda args: reserve(*args),
                [(self.ledger, "parallel-1"), (other, "parallel-2")],
            ))
        self.assertEqual(sum(result["ok"] is True for result in results), 1)
        self.assertEqual(self.ledger.active_reserved_notional(), "60")

    def test_ambiguous_submission_retains_reservation_until_reconciled(self):
        self.assertTrue(self.ledger.reserve("ambiguous-1", "25", "0", "100")["ok"])
        self.assertTrue(self.ledger.transition("ambiguous-1", "SUBMITTING")["ok"])
        self.assertTrue(self.ledger.transition(
            "ambiguous-1", "UNKNOWN", detail="submission timeout; reconcile before retry"
        )["ok"])
        blocked = self.ledger.reserve("next-order", "80", "0", "100")
        self.assertFalse(blocked["ok"])
        self.assertEqual(self.ledger.get("ambiguous-1")["state"], "UNKNOWN")
        self.assertEqual(self.ledger.active_reserved_notional(), "25")

    def test_only_confirmed_exchange_reflection_can_externalize_unknown_reservation(self):
        self.ledger.reserve("unknown-2", "25", "0", "100")
        self.ledger.transition("unknown-2", "SUBMITTING")
        self.ledger.transition("unknown-2", "UNKNOWN")
        result = self.ledger.transition(
            "unknown-2", "EXTERNALIZED",
            detail="confirmed_snapshot: operator verified authoritative snapshot includes this order",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(self.ledger.active_reserved_notional(), "0")

    def test_unknown_reservation_cannot_be_released_without_terminal_evidence(self):
        self.ledger.reserve("unknown-release", "20", "0", "100")
        self.ledger.transition("unknown-release", "SUBMITTING")
        self.ledger.transition("unknown-release", "UNKNOWN")
        result = self.ledger.transition("unknown-release", "RELEASED", detail="probably rejected")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "release_requires_terminal_confirmation")
        self.assertEqual(self.ledger.active_reserved_notional(), "20")

    def test_unknown_reservation_cannot_be_externalized_without_snapshot_evidence(self):
        self.ledger.reserve("unknown-externalized", "20", "0", "100")
        self.ledger.transition("unknown-externalized", "SUBMITTING")
        self.ledger.transition("unknown-externalized", "UNKNOWN")
        result = self.ledger.transition("unknown-externalized", "EXTERNALIZED", detail="I think it exists")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "externalization_requires_snapshot_confirmation")
        self.assertEqual(self.ledger.active_reserved_notional(), "20")

    def test_released_reservation_cannot_be_reused(self):
        self.ledger.reserve("order-released", "10", "0", "100")
        self.assertTrue(self.ledger.transition("order-released", "RELEASED")["ok"])
        result = self.ledger.reserve("order-released", "10", "0", "100")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "reservation_not_reusable")

    def test_duplicate_id_with_different_notional_fails_closed(self):
        self.ledger.reserve("same-id", "10", "0", "100")
        result = self.ledger.reserve("same-id", "11", "0", "100")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "reservation_id_conflict")

    def test_invalid_values_raise_instead_of_understating_risk(self):
        for value in ("NaN", "Infinity", "-1", True, "not-a-number"):
            with self.subTest(value=value):
                with self.assertRaises(RiskLedgerError):
                    self.ledger.reserve("bad-" + str(value), value, "0", "100")

    def test_database_survives_ledger_reconstruction(self):
        self.ledger.reserve("persistent-1", "12.5", "0", "100")
        reopened = SQLiteRiskLedger(self.db_path)
        row = reopened.get("persistent-1")
        self.assertIsNotNone(row)
        self.assertEqual(row["state"], "RESERVED")
        self.assertEqual(row["notional_usdt"], "12.5")

    def test_invalid_state_transition_does_not_release_reservation(self):
        self.ledger.reserve("state-1", "20", "0", "100")
        result = self.ledger.transition("state-1", "EXTERNALIZED")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "invalid_reservation_transition")
        self.assertEqual(self.ledger.active_reserved_notional(), "20")


    def test_list_unresolved_returns_active_states_oldest_first_without_mutation(self):
        self.ledger.reserve("older-unknown", "10", "0", "100")
        self.ledger.transition("older-unknown", "SUBMITTING")
        self.ledger.transition("older-unknown", "UNKNOWN")
        self.ledger.reserve("newer-reserved", "15", "0", "100")
        self.ledger.reserve("eventually-released", "5", "0", "100")
        self.ledger.transition("eventually-released", "RELEASED")
        rows = self.ledger.list_unresolved()
        self.assertEqual([row["order_id"] for row in rows], ["older-unknown", "newer-reserved"])
        self.assertEqual([row["state"] for row in rows], ["UNKNOWN", "RESERVED"])
        self.assertEqual(self.ledger.active_reserved_notional(), "25")
        self.assertEqual(self.ledger.get("eventually-released")["state"], "RELEASED")

    def test_recovery_audit_is_durable_append_only_and_bounded(self):
        first = self.ledger.record_recovery_audit(
            order_id="audit-order-1",
            symbol="BTCUSDT",
            state_before="UNKNOWN",
            outcome="deferred",
            reason="order_snapshot_ambiguous",
        )
        second = self.ledger.record_recovery_audit(
            order_id="audit-order-2",
            state_before="RESERVED",
            outcome="deferred",
            reason="crash_window_review_required",
        )
        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        reopened = SQLiteRiskLedger(self.db_path)
        rows = reopened.list_recovery_audit(limit=1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["order_id"], "audit-order-2")
        all_rows = reopened.list_recovery_audit(limit=10)
        self.assertEqual([row["order_id"] for row in all_rows], ["audit-order-2", "audit-order-1"])
        self.assertEqual(all_rows[1]["reason"], "order_snapshot_ambiguous")
        with self.assertRaises(RiskLedgerError):
            reopened.list_recovery_audit(limit=0)

    def test_list_unresolved_validates_limit_and_supports_bounded_batches(self):
        for invalid in (0, -1, 1001, True, 1.5, "10"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(RiskLedgerError):
                    self.ledger.list_unresolved(limit=invalid)
        for index in range(3):
            self.ledger.reserve(f"batch-{index}", "1", "0", "100")
        self.assertEqual(len(self.ledger.list_unresolved(limit=2)), 2)
        self.assertEqual(len(self.ledger.list_unresolved(limit=1)), 1)


if __name__ == "__main__":
    unittest.main()
