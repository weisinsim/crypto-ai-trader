import unittest

from notifications.pionex_reconcile import ReconciliationError, reconcile_reduction


class PionexReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.before = {"symbol": "BTC_USDT_PERP", "side": "LONG", "quantity": "1.5"}
        self.after = {"symbol": "BTC_USDT_PERP", "side": "LONG", "quantity": "0.5"}
        self.fills = [
            {"fill_id": "f2", "order_id": "o1", "symbol": "BTC_USDT_PERP",
             "side": "SELL", "position_side": "LONG", "effect": "CLOSE",
             "quantity": "0.4", "timestamp_ms": 1700000000001},
            {"fill_id": "f1", "order_id": "o1", "symbol": "BTC_USDT_PERP",
             "side": "SELL", "position_side": "LONG", "effect": "CLOSE",
             "quantity": "0.6", "timestamp_ms": 1700000000000},
        ]

    def reconcile(self, **kwargs):
        return reconcile_reduction(self.before, self.after, self.fills,
                                   same_account=True, coverage_complete=True, account_scope="account-A", position_epoch="position-1", **kwargs)

    def test_partial_exit_and_deterministic_id(self):
        event = self.reconcile()
        self.assertEqual(event["event_type"], "PARTIAL_EXIT")
        self.assertEqual(event["closed_quantity"], "1.0")
        self.assertEqual(event["classification"], "EXIT_REASON_UNVERIFIED")
        self.assertEqual(event["event_id"], reconcile_reduction(
            self.before, self.after, list(reversed(self.fills)),
            same_account=True, coverage_complete=True, account_scope="account-A", position_epoch="position-1")["event_id"])

    def test_account_and_position_scope_change_event_id(self):
        original = self.reconcile()["event_id"]
        self.assertNotEqual(original, reconcile_reduction(
            self.before, self.after, self.fills, same_account=True,
            coverage_complete=True, account_scope="account-B",
            position_epoch="position-1")["event_id"])
        self.assertNotEqual(original, reconcile_reduction(
            self.before, self.after, self.fills, same_account=True,
            coverage_complete=True, account_scope="account-A",
            position_epoch="position-2")["event_id"])

    def test_missing_account_scope_is_rejected(self):
        with self.assertRaises(ReconciliationError):
            reconcile_reduction(self.before, self.after, self.fills,
                                same_account=True, coverage_complete=True)

    def test_full_exit(self):
        self.after["quantity"] = "0"
        self.fills[0]["quantity"] = "0.9"
        self.assertEqual(self.reconcile()["event_type"], "FULL_EXIT")

    def test_no_reduction(self):
        self.after["quantity"] = "1.5"
        self.assertIsNone(self.reconcile())

    def test_missing_coverage_rejected(self):
        with self.assertRaises(ReconciliationError):
            reconcile_reduction(self.before, self.after, self.fills, same_account=True)
        with self.assertRaises(ReconciliationError):
            reconcile_reduction(self.before, self.after, self.fills, coverage_complete=True)

    def test_direction_does_not_prove_close(self):
        self.fills[0]["effect"] = "OPEN"
        with self.assertRaises(ReconciliationError):
            self.reconcile()

    def test_fill_total_must_match_position_delta(self):
        self.fills[0]["quantity"] = "0.3"
        with self.assertRaises(ReconciliationError):
            self.reconcile()

    def test_duplicate_fills_rejected(self):
        self.fills[0]["fill_id"] = "f1"
        with self.assertRaises(ReconciliationError):
            self.reconcile()

    def test_nonfinite_and_negative_quantities_rejected(self):
        for quantity in ("NaN", "Infinity", "-1", None):
            self.after["quantity"] = quantity
            with self.assertRaises(ReconciliationError):
                self.reconcile()

    def test_short_close_requires_buy(self):
        self.before["side"] = self.after["side"] = "SHORT"
        for fill in self.fills:
            fill["position_side"] = "SHORT"
            fill["side"] = "BUY"
        self.assertEqual(self.reconcile()["position_side"], "SHORT")


if __name__ == "__main__":
    unittest.main()
