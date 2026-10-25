import unittest
from decimal import Decimal

from execution.testnet_lifecycle import (
    cancel_testnet_sibling_after_flatten,
    ensure_testnet_protective_exits,
)


class SnapshotThenSubmissionTimeout:
    def __init__(self):
        self.snapshot_calls = 0
        self.submit_calls = 0

    def testnet_position_snapshot(self, symbol):
        self.snapshot_calls += 1
        return {
            "ok": True,
            "position_amount": "0.5",
            "entry_price": "100",
            "open_orders": [],
        }

    def submit_testnet_protective_exit(self, symbol, kind, quantity, price, client_id):
        self.submit_calls += 1
        raise TimeoutError("simulated response lost after request")


class QueryTimeout:
    def query_testnet_order(self, symbol, client_order_id):
        raise TimeoutError("simulated order lookup timeout")


class TestnetAmbiguousNetworkRecoveryTests(unittest.TestCase):
    def test_protective_submission_timeout_requires_reconciliation_and_no_retry(self):
        adapter = SnapshotThenSubmissionTimeout()
        result = ensure_testnet_protective_exits(
            adapter, "BTCUSDT", Decimal("99"), Decimal("102"), "run-timeout-1"
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "adapter_exception_requires_reconciliation")
        self.assertTrue(result["requires_reconciliation"])
        self.assertFalse(result["automatic_retry"])
        self.assertEqual(adapter.submit_calls, 1)
        self.assertEqual(adapter.snapshot_calls, 1)

    def test_order_query_timeout_fails_closed_without_cancel(self):
        adapter = QueryTimeout()
        result = cancel_testnet_sibling_after_flatten(
            adapter, "BTCUSDT", "btc-sl", "btc-tp"
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "adapter_exception_requires_reconciliation")
        self.assertTrue(result["requires_reconciliation"])
        self.assertFalse(result["automatic_retry"])


if __name__ == "__main__":
    unittest.main()
