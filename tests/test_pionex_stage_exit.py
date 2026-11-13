import tempfile
import unittest
from pathlib import Path

from notifications.pionex_exit_store import PionexExitStore
from notifications.pionex_stage_exit import stage_verified_exit
from notifications.pionex_fill_pages import PaginationError


class StageExitIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = PionexExitStore(Path(self.temp.name) / "events.db")
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
            "execution_status": "EXECUTED", "trigger_kind": "STOP_LOSS",
        }]
        self.flags = dict(
            account_scope="acct", position_epoch="epoch",
            expected_symbol="BTC_USDT_PERP", pages_complete=True,
            same_account=True, order_schema_verified=True,
            all_fills_matched=True, order_coverage_complete=True,
        )

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_stage_once_and_never_ready(self):
        first, inserted = stage_verified_exit(
            self.store, self.before, self.after, self.pages, self.orders,
            **self.flags)
        self.assertTrue(inserted)
        self.assertEqual(first["classification"], "STOP_LOSS")
        self.assertFalse(first["notification_ready"])
        second, inserted = stage_verified_exit(
            self.store, self.before, self.after, self.pages, self.orders,
            **self.flags)
        self.assertFalse(inserted)
        self.assertEqual(first["event_id"], second["event_id"])
        self.assertEqual(len(self.store.staged()), 1)

    def test_incomplete_page_does_not_write(self):
        self.flags["pages_complete"] = False
        with self.assertRaises(PaginationError):
            stage_verified_exit(
                self.store, self.before, self.after, self.pages, self.orders,
                **self.flags)
        self.assertEqual(self.store.staged(), [])

    def test_no_position_reduction_does_not_write(self):
        event, inserted = stage_verified_exit(
            self.store, self.before, self.before, self.pages, self.orders,
            **self.flags)
        self.assertIsNone(event)
        self.assertFalse(inserted)
        self.assertEqual(self.store.staged(), [])


if __name__ == "__main__":
    unittest.main()
