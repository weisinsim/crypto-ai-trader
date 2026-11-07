import unittest

from notifications.pionex_exit_assembly import (
    ExitEvidenceError, assemble_exit_evidence,
)
from notifications.pionex_exit_reason import ExitReasonError
from notifications.pionex_fill_pages import PaginationError


class ExitAssemblyTests(unittest.TestCase):
    def setUp(self):
        self.before = {"symbol": "BTC_USDT_PERP", "side": "LONG", "quantity": "2"}
        self.after = {"symbol": "BTC_USDT_PERP", "side": "LONG", "quantity": "1"}
        self.pages = [{
            "request_cursor": None, "next_cursor": None,
            "fills": [{
                "fill_id": "f1", "order_id": "o1", "symbol": "BTC_USDT_PERP",
                "side": "SELL", "position_side": "LONG", "effect": "CLOSE",
                "quantity": "1", "timestamp_ms": 1700000000000,
            }],
        }]
        self.orders = [{
            "order_id": "o1", "close_effect": "CLOSE",
            "execution_status": "EXECUTED", "trigger_kind": "TAKE_PROFIT",
        }]
        self.flags = {
            "account_scope": "acct", "position_epoch": "epoch",
            "expected_symbol": "BTC_USDT_PERP", "pages_complete": True,
            "same_account": True, "order_schema_verified": True,
            "all_fills_matched": True, "order_coverage_complete": True,
        }

    def assemble(self):
        return assemble_exit_evidence(
            self.before, self.after, self.pages, self.orders, **self.flags)

    def test_tp_stays_non_deliverable(self):
        event = self.assemble()
        self.assertEqual(event["classification"], "TAKE_PROFIT")
        self.assertEqual(event["event_type"], "PARTIAL_EXIT")
        self.assertIs(event["notification_ready"], False)

    def test_incomplete_pages_rejected(self):
        self.flags["pages_complete"] = False
        with self.assertRaises(PaginationError):
            self.assemble()

    def test_missing_order_evidence_rejected(self):
        self.orders[0]["order_id"] = "wrong"
        with self.assertRaises(ExitEvidenceError):
            self.assemble()

    def test_unverified_order_schema_rejected(self):
        self.flags["order_schema_verified"] = False
        with self.assertRaises(ExitReasonError):
            self.assemble()

    def test_no_reduction_does_not_claim_exit(self):
        self.after["quantity"] = "2"
        self.assertIsNone(self.assemble())


if __name__ == "__main__":
    unittest.main()
