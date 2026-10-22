import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from execution.testnet_lifecycle import ensure_testnet_protective_exits


class FakeAdapter:
    def __init__(self, position="0.5", entry="100", orders=None, fail_kind=None, mutate_position=False):
        self.position = position
        self.entry = entry
        self.orders = [dict(order) for order in (orders or [])]
        self.fail_kind = fail_kind
        self.submissions = []
        self.mutate_position = mutate_position
        self.snapshot_count = 0

    def testnet_position_snapshot(self, symbol):
        self.snapshot_count += 1
        if self.mutate_position and self.snapshot_count > 1:
            self.position = "0.4"
        return {
            "ok": True,
            "symbol": symbol,
            "position_amount": self.position,
            "entry_price": self.entry,
            "open_orders": [dict(order) for order in self.orders],
        }

    def submit_testnet_protective_exit(self, symbol, kind, quantity, price, client_id):
        self.submissions.append((symbol, kind, str(quantity), str(price), client_id))
        if kind == self.fail_kind:
            return {"accepted": False, "reason": "simulated_failure"}
        order_type = "STOP_MARKET" if kind == "stop_loss" else "TAKE_PROFIT_MARKET"
        self.orders.append({
            "status": "NEW", "reduce_only": True, "type": order_type,
            "stop_price": str(price), "client_order_id": client_id,
            "side": "SELL" if DecimalPosition(self.position) > 0 else "BUY",
            "orig_quantity": str(abs(DecimalPosition(self.position))),
        })
        return {"accepted": True, "client_order_id": client_id}


def DecimalPosition(value):
    from decimal import Decimal
    return Decimal(str(value))


class TestnetLifecycleTests(unittest.TestCase):
    def test_installs_stop_first_then_target_and_verifies_exchange_snapshot(self):
        adapter = FakeAdapter()
        result = ensure_testnet_protective_exits(adapter, "BTCUSDT", "99", "102", "btc-run-01")
        self.assertTrue(result["ok"])
        self.assertEqual(result["mode"], "testnet_protective_exits_verified")
        self.assertEqual([s[1] for s in adapter.submissions], ["stop_loss", "take_profit"])
        self.assertEqual(len(result["verified_orders"]), 2)
        self.assertEqual(adapter.snapshot_count, 2)
        self.assertTrue(result["requires_post_fill_sibling_cancellation"])

    def test_target_failure_leaves_stop_and_demands_reconciliation(self):
        adapter = FakeAdapter(fail_kind="take_profit")
        result = ensure_testnet_protective_exits(adapter, "BTCUSDT", "99", "102", "btc-run-02")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "take_profit_submission_failed_stop_remains")
        self.assertEqual([s[1] for s in adapter.submissions], ["stop_loss", "take_profit"])
        self.assertTrue(result["requires_reconciliation"])

    def test_existing_matching_stop_is_reused(self):
        adapter = FakeAdapter(orders=[{
            "status": "NEW", "reduce_only": True, "type": "STOP_MARKET",
            "stop_price": "99", "side": "SELL", "orig_quantity": "0.5",
        }])
        result = ensure_testnet_protective_exits(adapter, "BTCUSDT", "99", "102", "btc-run-03")
        self.assertTrue(result["ok"])
        self.assertEqual([s[1] for s in adapter.submissions], ["take_profit"])

    def test_mismatched_existing_stop_fails_closed(self):
        adapter = FakeAdapter(orders=[{
            "status": "NEW", "reduce_only": True, "type": "STOP_MARKET",
            "stop_price": "98", "side": "SELL", "orig_quantity": "0.5",
        }])
        result = ensure_testnet_protective_exits(adapter, "BTCUSDT", "99", "102", "btc-run-04")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "existing_stop_price_mismatch")
        self.assertEqual(adapter.submissions, [])

    def test_flat_position_does_not_submit_orders(self):
        adapter = FakeAdapter(position="0")
        result = ensure_testnet_protective_exits(adapter, "BTCUSDT", "99", "102", "btc-run-05")
        self.assertEqual(result["reason"], "no_open_position_or_invalid_values")
        self.assertEqual(adapter.submissions, [])

    def test_short_position_requires_target_below_entry_and_stop_above(self):
        adapter = FakeAdapter(position="-0.5", entry="100")
        result = ensure_testnet_protective_exits(adapter, "BTCUSDT", "101", "98", "btc-run-06")
        self.assertTrue(result["ok"])
        self.assertEqual([s[1] for s in adapter.submissions], ["stop_loss", "take_profit"])
        self.assertEqual(result["verified_orders"]["STOP_MARKET"]["side"], "BUY")

    def test_position_change_during_setup_fails_closed(self):
        adapter = FakeAdapter(mutate_position=True)
        result = ensure_testnet_protective_exits(adapter, "BTCUSDT", "99", "102", "btc-run-07")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "position_changed_during_protection_setup")
        self.assertTrue(result["requires_reconciliation"])

    def test_existing_order_quantity_mismatch_fails_closed(self):
        adapter = FakeAdapter(orders=[{
            "status": "NEW", "reduce_only": True, "type": "STOP_MARKET",
            "stop_price": "99", "side": "SELL", "orig_quantity": "0.3",
        }])
        result = ensure_testnet_protective_exits(adapter, "BTCUSDT", "99", "102", "btc-run-08")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "existing_stop_quantity_mismatch")
        self.assertEqual(adapter.submissions, [])

    def test_unexpected_active_entry_order_blocks_protective_setup(self):
        adapter = FakeAdapter(orders=[{
            "status": "NEW", "reduce_only": False, "type": "LIMIT",
            "stop_price": "0", "side": "BUY", "orig_quantity": "0.5",
        }])
        result = ensure_testnet_protective_exits(adapter, "BTCUSDT", "99", "102", "btc-run-09")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "unexpected_active_order_requires_reconciliation")
        self.assertEqual(adapter.submissions, [])

    def test_unknown_open_order_status_blocks_duplicate_protection(self):
        adapter = FakeAdapter(orders=[{
            "status": "PENDING_UNKNOWN", "reduce_only": True, "type": "STOP_MARKET",
            "stop_price": "99", "side": "SELL", "orig_quantity": "0.5",
        }])
        result = ensure_testnet_protective_exits(adapter, "BTCUSDT", "99", "102", "btc-run-10")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "unknown_or_malformed_open_order_requires_reconciliation")
        self.assertEqual(adapter.submissions, [])

    def test_non_boolean_reduce_only_value_fails_closed(self):
        adapter = FakeAdapter(orders=[{
            "status": "NEW", "reduce_only": 1, "type": "STOP_MARKET",
            "stop_price": "99", "side": "SELL", "orig_quantity": "0.5",
        }])
        result = ensure_testnet_protective_exits(adapter, "BTCUSDT", "99", "102", "btc-run-11")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "unknown_or_malformed_open_order_requires_reconciliation")
        self.assertEqual(adapter.submissions, [])


if __name__ == "__main__":
    unittest.main()


class FakeCancellationAdapter:
    def __init__(self, exit_status="FILLED", position="0", sibling_status="NEW",
                 cancel_result=None, fail_snapshot=False, post_cancel_status=None):
        self.exit_status = exit_status
        self.position = position
        self.sibling_status = sibling_status
        self.cancel_result = cancel_result or {"cancelled": True}
        self.fail_snapshot = fail_snapshot
        self.post_cancel_status = post_cancel_status
        self.cancel_calls = []
        self.snapshot_calls = 0

    def query_testnet_order(self, symbol, client_order_id):
        if client_order_id == "exit-01":
            return {"found": True, "status": self.exit_status, "executed_quantity": "0.5"}
        return {"found": True, "status": self.sibling_status}

    def testnet_position_snapshot(self, symbol):
        self.snapshot_calls += 1
        if self.fail_snapshot:
            return {"ok": False}
        return {"ok": True, "position_amount": self.position, "open_orders": []}

    def cancel_testnet_order(self, symbol, client_order_id):
        self.cancel_calls.append((symbol, client_order_id))
        if self.cancel_result.get("cancelled"):
            self.sibling_status = self.post_cancel_status or "CANCELED"
        return self.cancel_result


class TestnetSiblingCancellationTests(unittest.TestCase):
    def test_partial_exit_does_not_cancel_sibling(self):
        from execution.testnet_lifecycle import cancel_testnet_sibling_after_flatten
        adapter = FakeCancellationAdapter(exit_status="PARTIALLY_FILLED", position="0.2")
        result = cancel_testnet_sibling_after_flatten(adapter, "BTCUSDT", "exit-01", "exit-02")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "exit_not_fully_filled")
        self.assertEqual(adapter.cancel_calls, [])

    def test_non_flat_position_keeps_sibling_protection(self):
        from execution.testnet_lifecycle import cancel_testnet_sibling_after_flatten
        adapter = FakeCancellationAdapter(position="0.2")
        result = cancel_testnet_sibling_after_flatten(adapter, "BTCUSDT", "exit-01", "exit-02")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "position_not_flat_sibling_must_remain")
        self.assertEqual(adapter.cancel_calls, [])

    def test_confirmed_flat_position_cancels_sibling(self):
        from execution.testnet_lifecycle import cancel_testnet_sibling_after_flatten
        adapter = FakeCancellationAdapter()
        result = cancel_testnet_sibling_after_flatten(adapter, "BTCUSDT", "exit-01", "exit-02")
        self.assertTrue(result["ok"], result)
        self.assertEqual(adapter.cancel_calls, [("BTCUSDT", "exit-02")])
        self.assertEqual(adapter.snapshot_calls, 2)

    def test_cancel_acknowledgement_without_inactive_order_status_fails_closed(self):
        from execution.testnet_lifecycle import cancel_testnet_sibling_after_flatten
        adapter = FakeCancellationAdapter(post_cancel_status="FILLED")
        result = cancel_testnet_sibling_after_flatten(adapter, "BTCUSDT", "exit-01", "exit-02")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "post_cancel_sibling_not_confirmed_inactive")
        self.assertTrue(result["requires_reconciliation"])

    def test_cancel_failure_requires_reconciliation(self):
        from execution.testnet_lifecycle import cancel_testnet_sibling_after_flatten
        adapter = FakeCancellationAdapter(cancel_result={"cancelled": False, "reason": "timeout"})
        result = cancel_testnet_sibling_after_flatten(adapter, "BTCUSDT", "exit-01", "exit-02")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "sibling_cancel_unconfirmed")
        self.assertTrue(result["requires_reconciliation"])


    def test_both_exits_filled_requires_exchange_history_reconciliation(self):
        from execution.testnet_lifecycle import cancel_testnet_sibling_after_flatten
        adapter = FakeCancellationAdapter(sibling_status="FILLED")
        result = cancel_testnet_sibling_after_flatten(adapter, "BTCUSDT", "exit-01", "exit-02")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "both_exits_report_filled_check_exchange_history")
        self.assertEqual(adapter.cancel_calls, [])

    def test_sibling_lookup_failure_does_not_cancel_blindly(self):
        from execution.testnet_lifecycle import cancel_testnet_sibling_after_flatten

        class MissingSiblingAdapter(FakeCancellationAdapter):
            def query_testnet_order(self, symbol, client_order_id):
                if client_order_id == "exit-01":
                    return {"found": True, "status": "FILLED", "executed_quantity": "0.5"}
                return {"found": False}

        adapter = MissingSiblingAdapter()
        result = cancel_testnet_sibling_after_flatten(adapter, "BTCUSDT", "exit-01", "exit-02")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "sibling_lookup_failed")
        self.assertEqual(adapter.cancel_calls, [])

    def test_post_cancel_position_change_requires_reconciliation(self):
        from execution.testnet_lifecycle import cancel_testnet_sibling_after_flatten

        class PositionChangesAfterCancelAdapter(FakeCancellationAdapter):
            def testnet_position_snapshot(self, symbol):
                self.snapshot_calls += 1
                amount = "0" if self.snapshot_calls == 1 else "0.1"
                return {"ok": True, "position_amount": amount, "open_orders": []}

        adapter = PositionChangesAfterCancelAdapter()
        result = cancel_testnet_sibling_after_flatten(adapter, "BTCUSDT", "exit-01", "exit-02")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "position_reopened_or_not_flat_after_cancel")
        self.assertTrue(result["requires_reconciliation"])


class TestnetPostSubmissionSnapshotValidationTests(unittest.TestCase):
    def test_unexpected_active_order_in_final_snapshot_fails_closed(self):
        class UnexpectedOrderAfterSubmitAdapter(FakeAdapter):
            def testnet_position_snapshot(self, symbol):
                snapshot = super().testnet_position_snapshot(symbol)
                if self.snapshot_count > 1:
                    snapshot["open_orders"].append({
                        "status": "NEW", "reduce_only": False, "type": "LIMIT",
                        "side": "BUY", "orig_quantity": "0.1",
                    })
                return snapshot

        adapter = UnexpectedOrderAfterSubmitAdapter()
        result = ensure_testnet_protective_exits(
            adapter, "BTCUSDT", "99", "102", "btc-run-final-order"
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "post_submission_unexpected_active_order")
        self.assertTrue(result["requires_reconciliation"])

    def test_non_boolean_reduce_only_in_final_snapshot_fails_closed(self):
        class MalformedFinalOrderAdapter(FakeAdapter):
            def testnet_position_snapshot(self, symbol):
                snapshot = super().testnet_position_snapshot(symbol)
                if self.snapshot_count > 1:
                    snapshot["open_orders"][0]["reduce_only"] = 1
                return snapshot

        adapter = MalformedFinalOrderAdapter()
        result = ensure_testnet_protective_exits(
            adapter, "BTCUSDT", "99", "102", "btc-final-malformed"
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "post_submission_order_unknown_or_malformed")
        self.assertTrue(result["requires_reconciliation"])
