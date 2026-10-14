import unittest

from validation.paper_execution import PaperExecutionBook
from validation.paper_reconciliation import reconcile_paper_positions


class PaperReconciliationTests(unittest.TestCase):
    def test_partial_cancel_remains_exposure_until_protective_exit(self):
        book = PaperExecutionBook()
        book.place_order("long-partial", "BTCUSDT", "LONG", 2, 90, 120)
        book.record_fill("long-partial", 0.5, 100)
        book.cancel("long-partial")

        positions = reconcile_paper_positions(book.orders)
        self.assertEqual(positions["BTCUSDT"]["net_quantity"], 0.5)
        self.assertEqual(positions["BTCUSDT"]["open_quantity"], 0.5)
        self.assertEqual(positions["BTCUSDT"]["legs"][0]["status"], "CANCELLED")

        book.evaluate_protective_exit("long-partial", {"h": 105, "l": 85})
        positions = reconcile_paper_positions(book.orders)
        self.assertEqual(positions["BTCUSDT"]["net_quantity"], 0.0)
        self.assertEqual(positions["BTCUSDT"]["open_quantity"], 0.0)

    def test_long_and_short_are_net_signed_but_gross_is_preserved(self):
        book = PaperExecutionBook()
        book.place_order("long", "BTCUSDT", "LONG", 2, 90, 120)
        book.record_fill("long", 1, 100)
        book.place_order("short", "BTCUSDT", "SHORT", 1, 120, 80)
        book.record_fill("short", 0.25, 100)
        result = reconcile_paper_positions(book.orders)["BTCUSDT"]
        self.assertAlmostEqual(result["net_quantity"], 0.75)
        self.assertAlmostEqual(result["open_quantity"], 1.25)
        self.assertEqual(len(result["legs"]), 2)

    def test_closed_and_unfilled_orders_do_not_create_exposure(self):
        book = PaperExecutionBook()
        book.place_order("empty", "ETHUSDT", "LONG", 1, 90, 120)
        book.place_order("closed", "BTCUSDT", "SHORT", 1, 120, 80)
        book.record_fill("closed", 1, 100)
        book.evaluate_protective_exit("closed", {"h": 110, "l": 75})
        result = reconcile_paper_positions(book.orders)
        self.assertEqual(result["BTCUSDT"]["net_quantity"], 0.0)
        self.assertEqual(result["ETHUSDT"]["net_quantity"], 0.0)
        self.assertEqual(result["ETHUSDT"]["open_quantity"], 0.0)

    def test_malformed_ledger_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "inconsistent filled order"):
            reconcile_paper_positions({
                "bad": {
                    "symbol": "BTCUSDT", "side": "LONG", "status": "FILLED",
                    "quantity": 2, "filled_quantity": 1,
                }
            })


if __name__ == "__main__":
    unittest.main()
