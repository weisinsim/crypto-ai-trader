import json
import tempfile
import unittest
from pathlib import Path

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

    def test_fill_event_is_idempotent_and_conflicts_are_rejected(self):
        self.place_long()
        first = self.book.record_fill("paper-1", 0.5, 100, fill_id="fill-001")
        repeated = self.book.record_fill("paper-1", 0.5, 100, fill_id="fill-001")
        self.assertEqual(first, repeated)
        self.assertEqual(repeated["filled_quantity"], 0.5)
        with self.assertRaisesRegex(ValueError, "different fill parameters"):
            self.book.record_fill("paper-1", 0.4, 100, fill_id="fill-001")
        with self.assertRaisesRegex(ValueError, "fill_id"):
            self.book.record_fill("paper-1", 0.1, 100, fill_id="")

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

    def test_closed_order_rejects_duplicate_protective_exit(self):
        self.place_long()
        self.book.record_fill("paper-1", 2, 100)
        first = self.book.evaluate_protective_exit("paper-1", {"h": 125, "l": 95})
        self.assertEqual(first["status"], "CLOSED")
        with self.assertRaisesRegex(ValueError, "fully filled"):
            self.book.evaluate_protective_exit("paper-1", {"h": 130, "l": 80})
        self.assertEqual(self.book.get("paper-1")["exit_price"], 120)

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

    def test_persisted_state_survives_restart_and_preserves_idempotency(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "paper-ledger.json"
            book = PaperExecutionBook(path)
            book.place_order("persisted", "BTCUSDT", "LONG", 2, 90, 120)
            book.record_fill("persisted", 1, 100)
            restarted = PaperExecutionBook(path)
            self.assertEqual(restarted.get("persisted")["status"], "PARTIALLY_FILLED")
            self.assertEqual(restarted.get("persisted")["filled_quantity"], 1)
            self.assertEqual(restarted.place_order("persisted", "BTCUSDT", "LONG", 2, 90, 120)["status"], "PARTIALLY_FILLED")
            restarted.record_fill("persisted", 1, 110)
            restarted.evaluate_protective_exit("persisted", {"h": 125, "l": 95})
            final = PaperExecutionBook(path).get("persisted")
            self.assertEqual(final["status"], "CLOSED")
            self.assertEqual(final["exit_reason"], "TAKE_PROFIT")

    def test_fill_event_idempotency_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.json"
            book = PaperExecutionBook(path)
            book.place_order("fill-restart", "BTCUSDT", "LONG", 2, 90, 120)
            book.record_fill("fill-restart", 1, 100, fill_id="exchange-fill-1")
            restarted = PaperExecutionBook(path)
            duplicate = restarted.record_fill("fill-restart", 1, 100, fill_id="exchange-fill-1")
            self.assertEqual(duplicate["filled_quantity"], 1)
            self.assertEqual(duplicate["status"], "PARTIALLY_FILLED")
            with self.assertRaisesRegex(ValueError, "different fill parameters"):
                restarted.record_fill("fill-restart", 0.5, 101, fill_id="exchange-fill-1")

    def test_schema_v1_ledger_migrates_to_v2_on_next_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy.json"
            legacy_order = {
                "client_order_id": "legacy", "symbol": "BTCUSDT", "side": "LONG",
                "quantity": 1.0, "stop_loss": 90.0, "take_profit": 120.0,
                "status": "OPEN", "filled_quantity": 0.0, "average_fill_price": None,
                "exit_price": None, "exit_reason": None,
            }
            path.write_text(json.dumps({"schema_version": 1, "orders": {"legacy": legacy_order}}), encoding="utf-8")
            book = PaperExecutionBook(path)
            self.assertEqual(book.get("legacy")["fill_events"], {})
            book.record_fill("legacy", 1, 100, fill_id="new-fill")
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["schema_version"], 2)
            self.assertIn("new-fill", saved["orders"]["legacy"]["fill_events"])

    def test_corrupt_or_unknown_schema_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.json"
            path.write_text("{not-json", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unreadable"):
                PaperExecutionBook(path)
            path.write_text(json.dumps({"schema_version": 999, "orders": {}}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "schema"):
                PaperExecutionBook(path)

    def test_save_failure_rolls_back_in_memory_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missing-parent" / "ledger.json"
            book = PaperExecutionBook(path)
            book.place_order("rollback", "BTCUSDT", "LONG", 1, 90, 120)
            # Turn the parent path into a regular file so atomic persistence fails.
            path.parent.parent.mkdir(parents=True, exist_ok=True)
            path.parent.parent.joinpath("blocker").write_text("x", encoding="utf-8")
            blocked_path = path.parent.parent / "blocker" / "ledger.json"
            blocked = PaperExecutionBook(blocked_path)
            blocked.orders = dict(book.orders)
            with self.assertRaises(OSError):
                blocked.record_fill("rollback", 1, 100)
            self.assertEqual(blocked.get("rollback")["status"], "OPEN")


if __name__ == "__main__":
    unittest.main()
