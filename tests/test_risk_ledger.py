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

    def tearDown(self):
        self.temp_dir.cleanup()

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
            detail="operator confirmed authoritative snapshot includes this order",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(self.ledger.active_reserved_notional(), "0")

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


if __name__ == "__main__":
    unittest.main()
