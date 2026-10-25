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
            "snapshot_started_at_ms": int(__import__("time").time() * 1000) - 10,
            "snapshot_completed_at_ms": int(__import__("time").time() * 1000),
            "snapshot_duration_ms": 10.0,
            "open_orders": [],
        }

    def submit_testnet_protective_exit(self, symbol, kind, quantity, price, client_id):
        self.submit_calls += 1
        raise TimeoutError("simulated response lost after request")


class PostSubmissionSnapshotTimeout:
    def __init__(self):
        self.snapshot_calls = 0
        self.submitted_client_ids = []

    def testnet_position_snapshot(self, symbol):
        self.snapshot_calls += 1
        if self.snapshot_calls == 1:
            return {
                "ok": True,
                "position_amount": "0.5",
                "entry_price": "100",
                "snapshot_started_at_ms": int(__import__("time").time() * 1000) - 10,
                "snapshot_completed_at_ms": int(__import__("time").time() * 1000),
                "snapshot_duration_ms": 10.0,
                "open_orders": [],
            }
        raise TimeoutError("simulated verification snapshot timeout")

    def submit_testnet_protective_exit(self, symbol, kind, quantity, price, client_id):
        self.submitted_client_ids.append(client_id)
        return {"accepted": True, "client_order_id": client_id}


class QueryTimeout:
    def query_testnet_order(self, symbol, client_order_id):
        raise TimeoutError("simulated order lookup timeout")


class CancelAcceptedThenTimeout:
    def __init__(self):
        self.cancel_calls = 0
        self.query_calls = 0
        self.snapshot_calls = 0

    def query_testnet_order(self, symbol, client_order_id):
        self.query_calls += 1
        if client_order_id == "btc-sl":
            return {
                "found": True,
                "status": "FILLED",
                "executed_quantity": "0.5",
            }
        if self.query_calls == 2:
            return {"found": True, "status": "NEW"}
        raise TimeoutError("simulated post-cancel lookup timeout")

    def testnet_position_snapshot(self, symbol):
        self.snapshot_calls += 1
        return {"ok": True, "position_amount": "0"}

    def cancel_testnet_order(self, symbol, client_order_id):
        self.cancel_calls += 1
        raise TimeoutError("simulated cancel response lost after request")


class PartialFillLeavesExposure:
    def __init__(self):
        self.cancel_calls = 0

    def query_testnet_order(self, symbol, client_order_id):
        return {"found": True, "status": "FILLED", "executed_quantity": "0.5"}

    def testnet_position_snapshot(self, symbol):
        return {"ok": True, "position_amount": "0.3"}

    def cancel_testnet_order(self, symbol, client_order_id):
        self.cancel_calls += 1
        return {"cancelled": True}


class SiblingFillsDuringCancelRace:
    def __init__(self):
        self.query_calls = 0
        self.cancel_calls = 0

    def query_testnet_order(self, symbol, client_order_id):
        self.query_calls += 1
        if client_order_id == "btc-sl":
            return {"found": True, "status": "FILLED", "executed_quantity": "0.5"}
        if self.query_calls == 3:
            return {"found": True, "status": "FILLED", "executed_quantity": "0.5"}
        return {"found": True, "status": "NEW"}

    def testnet_position_snapshot(self, symbol):
        return {"ok": True, "position_amount": "0"}

    def cancel_testnet_order(self, symbol, client_order_id):
        self.cancel_calls += 1
        return {"cancelled": True}


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

    def test_post_submission_verification_timeout_requires_reconciliation_without_resubmit(self):
        adapter = PostSubmissionSnapshotTimeout()
        result = ensure_testnet_protective_exits(
            adapter, "BTCUSDT", Decimal("99"), Decimal("102"), "run-verify-timeout"
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "adapter_exception_requires_reconciliation")
        self.assertTrue(result["requires_reconciliation"])
        self.assertFalse(result["automatic_retry"])
        self.assertEqual(adapter.snapshot_calls, 2)
        self.assertEqual(adapter.submitted_client_ids, ["run-verify-timeout-sl", "run-verify-timeout-tp"])

    def test_order_query_timeout_fails_closed_without_cancel(self):
        adapter = QueryTimeout()
        result = cancel_testnet_sibling_after_flatten(
            adapter, "BTCUSDT", "btc-sl", "btc-tp"
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "adapter_exception_requires_reconciliation")
        self.assertTrue(result["requires_reconciliation"])
        self.assertFalse(result["automatic_retry"])

    def test_partial_exit_never_cancels_sibling_while_exposure_remains(self):
        adapter = PartialFillLeavesExposure()
        result = cancel_testnet_sibling_after_flatten(
            adapter, "BTCUSDT", "btc-sl", "btc-tp"
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "position_not_flat_sibling_must_remain")
        self.assertEqual(adapter.cancel_calls, 0)

    def test_sibling_fill_race_requires_reconciliation_after_cancel_ack(self):
        adapter = SiblingFillsDuringCancelRace()
        result = cancel_testnet_sibling_after_flatten(
            adapter, "BTCUSDT", "btc-sl", "btc-tp"
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "post_cancel_sibling_not_confirmed_inactive")
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(adapter.cancel_calls, 1)

    def test_cancel_timeout_after_possible_acceptance_is_not_retried(self):
        adapter = CancelAcceptedThenTimeout()
        result = cancel_testnet_sibling_after_flatten(
            adapter, "BTCUSDT", "btc-sl", "btc-tp"
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "adapter_exception_requires_reconciliation")
        self.assertTrue(result["requires_reconciliation"])
        self.assertFalse(result["automatic_retry"])
        self.assertEqual(adapter.cancel_calls, 1)
        self.assertEqual(adapter.query_calls, 2)
        self.assertEqual(adapter.snapshot_calls, 1)


if __name__ == "__main__":
    unittest.main()
