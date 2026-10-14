import unittest

from validation.paper_execution import PaperExecutionBook


class PaperExecutionBookTests(unittest.TestCase):
    def setUp(self):
        self.book = PaperExecutionBook()

    def place_long(self):
        return self.book.place_order("paper-1", "BTCUSDT", "LONG", 2, 90, 120)

    def test_order_creation_is_idempotent(self):
        first = self.place_long()
        second = self.place_long()
        self.assertEqual(first, second)
        self.assertEqual(len(self.book.orders), 1)

    def test_reused_id_with_different_parameters_is_rejected(self):
        self.place_long()
        with self.assertRaisesRegex(ValueError, "reused with different"):
            self.book.place_order("paper-1", "BTCUSDT", "LONG", 3, 90, 120)

    def test_partial_fills_compute_weighted_average_and_complete(self):
        self.place_long()
        partial = self.book.record_fill("paper-1", 0.5, 100)
        self.assertEqual(partial["status"], "PARTIALLY_FILLED")
        self.assertAlmostEqual(partial["average_fill_price"], 100)
        final = self.book.record_fill("paper-1", 1.5, 110)
        self.assertEqual(final["status"], "FILLED")
        self.assertAlmostEqual(final["average_fill_price"], 107.5)

    def test_overfill_and_fill_after_cancel_are_rejected(self):
        self.place_long()
        with self.assertRaisesRegex(ValueError, "exceeds remaining"):
            self.book.record_fill("paper-1", 2.1, 100)
        self.book.cancel("paper-1")
        with self.assertRaisesRegex(ValueError, "not fillable"):
            self.book.record_fill("paper-1", 1, 100)

    def test_long_stop_has_priority_when_both_levels_touched(self):
        self.place_long()
        self.book.record_fill("paper-1", 2, 100)
        closed = self.book.evaluate_protective_exit("paper-1", {"h": 125, "l": 85})
        self.assertEqual(closed["status"], "CLOSED")
        self.assertEqual(closed["exit_reason"], "STOP")
        self.assertEqual(closed["exit_price"], 90)

    def test_short_take_profit_and_no_exit(self):
        self.book.place_order("paper-short", "ETHUSDT", "SHORT", 1, 120, 80)
        self.book.record_fill("paper-short", 1, 100)
        unchanged = self.book.evaluate_protective_exit("paper-short", {"h": 110, "l": 90})
        self.assertEqual(unchanged["status"], "FILLED")
        closed = self.book.evaluate_protective_exit("paper-short", {"h": 105, "l": 75})
        self.assertEqual(closed["exit_reason"], "TAKE_PROFIT")
        self.assertEqual(closed["exit_price"], 80)

    def test_invalid_risk_geometry_and_non_finite_values_are_rejected(self):
        for args in (
            ("bad-long", "BTCUSDT", "LONG", 1, 120, 90),
            ("bad-short", "BTCUSDT", "SHORT", 1, 80, 120),
            ("bad-qty", "BTCUSDT", "LONG", float("nan"), 90, 120),
        ):
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    self.book.place_order(*args)

    def test_protective_exit_requires_fully_filled_order(self):
        self.place_long()
        with self.assertRaisesRegex(ValueError, "fully filled"):
            self.book.evaluate_protective_exit("paper-1", {"h": 130, "l": 80})


if __name__ == "__main__":
    unittest.main()
