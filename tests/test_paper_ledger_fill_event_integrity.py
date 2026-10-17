"""Regression tests for persisted paper-ledger fill-event integrity."""
import json
import tempfile
import unittest
from pathlib import Path

from validation.paper_execution import PaperExecutionBook


class PaperLedgerFillEventIntegrityTests(unittest.TestCase):
    def test_load_rejects_fill_events_exceeding_recorded_filled_quantity(self):
        order = {
            "client_order_id": "paper-integrity",
            "symbol": "BTCUSDT",
            "side": "LONG",
            "quantity": 2.0,
            "stop_loss": 90.0,
            "take_profit": 120.0,
            "status": "PARTIALLY_FILLED",
            "filled_quantity": 0.5,
            "average_fill_price": 100.0,
            "exit_price": None,
            "exit_reason": None,
            "fill_events": {
                "fill-1": {"quantity": 0.4, "price": 100.0},
                "fill-2": {"quantity": 0.2, "price": 101.0},
            },
            "cancelled_remainder": False,
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.json"
            path.write_text(
                json.dumps({"schema_version": 3, "orders": {"paper-integrity": order}}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "fill event quantity exceeds recorded filled quantity"):
                PaperExecutionBook(path)

    def test_load_accepts_fill_events_within_recorded_filled_quantity(self):
        order = {
            "client_order_id": "paper-integrity",
            "symbol": "BTCUSDT",
            "side": "LONG",
            "quantity": 2.0,
            "stop_loss": 90.0,
            "take_profit": 120.0,
            "status": "PARTIALLY_FILLED",
            "filled_quantity": 0.5,
            "average_fill_price": 100.0,
            "exit_price": None,
            "exit_reason": None,
            "fill_events": {"fill-1": {"quantity": 0.25, "price": 100.0}},
            "cancelled_remainder": False,
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.json"
            path.write_text(
                json.dumps({"schema_version": 3, "orders": {"paper-integrity": order}}),
                encoding="utf-8",
            )
            restored = PaperExecutionBook(path)
            self.assertEqual(restored.get("paper-integrity")["filled_quantity"], 0.5)


if __name__ == "__main__":
    unittest.main()
