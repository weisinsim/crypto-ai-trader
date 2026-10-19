import unittest

from execution.order_lifecycle import apply_order_update, initial_order_state


class OrderLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.record = initial_order_state("client-123")

    def update(self, event_id, state, quantity):
        return {"event_id": event_id, "state": state,
                "cumulative_filled_qty": quantity}

    def test_ack_partial_then_filled(self):
        record, outcome = apply_order_update(
            self.record, self.update("e1", "ACKNOWLEDGED", 0))
        self.assertEqual(outcome, "applied")
        record, outcome = apply_order_update(
            record, self.update("e2", "PARTIALLY_FILLED", 0.4))
        self.assertEqual(outcome, "applied")
        record, outcome = apply_order_update(
            record, self.update("e3", "FILLED", 1.0))
        self.assertEqual(outcome, "applied")
        self.assertEqual(record["state"], "FILLED")
        self.assertEqual(record["cumulative_filled_qty"], 1.0)
        self.assertFalse(record["requires_reconciliation"])

    def test_duplicate_event_is_idempotent(self):
        updated, _ = apply_order_update(
            self.record, self.update("e1", "ACKNOWLEDGED", 0))
        duplicate, outcome = apply_order_update(
            updated, self.update("e1", "FILLED", 1))
        self.assertEqual(outcome, "duplicate_event_ignored")
        self.assertEqual(duplicate, updated)

    def test_fill_quantity_cannot_decrease(self):
        updated, _ = apply_order_update(
            self.record, self.update("e1", "PARTIALLY_FILLED", 0.5))
        result, outcome = apply_order_update(
            updated, self.update("e2", "PARTIALLY_FILLED", 0.2))
        self.assertEqual(outcome, "non_monotonic_fill_requires_reconciliation")
        self.assertEqual(result["state"], "RECONCILIATION_REQUIRED")
        self.assertTrue(result["requires_reconciliation"])

    def test_illegal_transition_fails_closed(self):
        result, outcome = apply_order_update(
            self.record, self.update("e1", "CANCELED", 0))
        self.assertEqual(outcome, "applied")
        result, outcome = apply_order_update(
            result, self.update("e2", "ACKNOWLEDGED", 0))
        self.assertEqual(outcome, "illegal_transition_requires_reconciliation")
        self.assertEqual(result["state"], "RECONCILIATION_REQUIRED")

    def test_filled_without_quantity_fails_closed(self):
        result, outcome = apply_order_update(
            self.record, self.update("e1", "FILLED", 0))
        self.assertEqual(outcome, "filled_without_quantity_requires_reconciliation")
        self.assertTrue(result["requires_reconciliation"])

    def test_malformed_update_fails_closed(self):
        result, outcome = apply_order_update(
            self.record, {"event_id": "e1", "state": "FILLED",
                          "cumulative_filled_qty": float("nan")})
        self.assertEqual(outcome, "invalid_update_requires_reconciliation")
        self.assertEqual(result["state"], "RECONCILIATION_REQUIRED")

    def test_missing_event_id_fails_closed(self):
        result, outcome = apply_order_update(
            self.record, {"state": "ACKNOWLEDGED", "cumulative_filled_qty": 0})
        self.assertEqual(outcome, "event_id_required")
        self.assertTrue(result["requires_reconciliation"])

    def test_initial_record_requires_client_order_id(self):
        with self.assertRaises(ValueError):
            initial_order_state(" ")


if __name__ == "__main__":
    unittest.main()
