import unittest

from execution.testnet_lifecycle import cancel_testnet_sibling_after_flatten, ensure_testnet_protective_exits, reconcile_testnet_restart


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
        import time
        now_ms = int(time.time() * 1000)
        return {
            "ok": True,
            "symbol": symbol,
            "position_amount": self.position,
            "entry_price": self.entry,
            "snapshot_started_at_ms": now_ms - 10,
            "snapshot_completed_at_ms": now_ms,
            "snapshot_duration_ms": 10.0,
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

    def test_stop_submission_failure_requires_reconciliation_and_forbids_retry(self):
        adapter = FakeAdapter(fail_kind="stop_loss")
        result = ensure_testnet_protective_exits(adapter, "BTCUSDT", "99", "102", "btc-run-02a")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "stop_submission_failed_requires_reconciliation")
        self.assertTrue(result["requires_reconciliation"])
        self.assertFalse(result["automatic_retry"])
        self.assertEqual(len(adapter.submissions), 1)

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


    def test_sibling_cancel_requires_filled_exit_and_flat_position(self):
        class ReconcileAdapter:
            def __init__(self, status="FILLED", position="0", sibling_status="NEW", cancel_ok=True):
                self.status = status
                self.position = position
                self.sibling_status = sibling_status
                self.cancel_ok = cancel_ok
                self.cancel_calls = []
                self.cancelled = False

            def query_testnet_order(self, symbol, client_id):
                if client_id == "btc-sl":
                    status = self.status
                else:
                    status = "CANCELED" if self.cancelled else self.sibling_status
                return {"found": True, "status": status, "executed_quantity": "0.5"}

            def testnet_position_snapshot(self, symbol):
                return {"ok": True, "position_amount": self.position}

            def cancel_testnet_order(self, symbol, client_id):
                self.cancel_calls.append((symbol, client_id))
                self.cancelled = self.cancel_ok
                return {"cancelled": self.cancel_ok, "status": "CANCELED" if self.cancel_ok else "UNKNOWN"}

        adapter = ReconcileAdapter()
        result = cancel_testnet_sibling_after_flatten(adapter, "BTCUSDT", "btc-sl", "btc-tp")
        self.assertTrue(result["ok"])
        self.assertEqual(adapter.cancel_calls, [("BTCUSDT", "btc-tp")])

    def test_sibling_is_not_cancelled_on_partial_fill_or_nonflat_position(self):
        class ReconcileAdapter:
            def __init__(self, status, position):
                self.status = status
                self.position = position
                self.cancel_calls = []

            def query_testnet_order(self, symbol, client_id):
                return {"found": True, "status": self.status, "executed_quantity": "0.2"}

            def testnet_position_snapshot(self, symbol):
                return {"ok": True, "position_amount": self.position}

            def cancel_testnet_order(self, symbol, client_id):
                self.cancel_calls.append(client_id)
                return {"cancelled": True}

        partial = ReconcileAdapter("PARTIALLY_FILLED", "0.3")
        result = cancel_testnet_sibling_after_flatten(partial, "BTCUSDT", "btc-sl", "btc-tp")
        self.assertEqual(result["reason"], "exit_not_fully_filled")
        self.assertEqual(partial.cancel_calls, [])

        nonflat = ReconcileAdapter("FILLED", "0.1")
        result = cancel_testnet_sibling_after_flatten(nonflat, "BTCUSDT", "btc-sl", "btc-tp")
        self.assertEqual(result["reason"], "position_not_flat_sibling_must_remain")
        self.assertEqual(nonflat.cancel_calls, [])


    def test_restart_reconciliation_queries_known_ids_without_mutation(self):
        import time

        class RestartAdapter:
            def __init__(self):
                self.query_calls = []
            def testnet_position_snapshot(self, symbol):
                now = int(time.time() * 1000)
                return {
                    "ok": True, "symbol": symbol, "position_amount": "0.5",
                    "entry_price": "100",
                    "snapshot_started_at_ms": now - 10,
                    "snapshot_completed_at_ms": now,
                    "snapshot_duration_ms": 10.0,
                    "open_orders": [{"client_order_id": "btc-sl", "status": "NEW"}],
                }
            def query_testnet_order(self, symbol, client_id):
                self.query_calls.append((symbol, client_id))
                return {"found": True, "client_order_id": client_id,
                        "exchange_order_id": "123", "status": "NEW"}
            def submit_testnet_protective_exit(self, *args):
                raise AssertionError("restart reconciliation must never submit orders")
            def cancel_testnet_order(self, *args):
                raise AssertionError("restart reconciliation must never cancel orders")

        adapter = RestartAdapter()
        result = reconcile_testnet_restart(adapter, "BTCUSDT", ["btc-sl"])
        self.assertTrue(result["ok"])
        self.assertFalse(result["automatic_order_submission_authorized"])
        self.assertFalse(result["requires_reconciliation"])
        self.assertEqual(adapter.query_calls, [("BTCUSDT", "btc-sl")])

    def test_restart_reconciliation_fails_closed_on_unknown_or_mismatched_state(self):
        import time

        class RestartAdapter:
            def testnet_position_snapshot(self, symbol):
                now = int(time.time() * 1000)
                return {
                    "ok": True, "symbol": symbol, "position_amount": "0.5",
                    "entry_price": "100",
                    "snapshot_started_at_ms": now - 10,
                    "snapshot_completed_at_ms": now,
                    "snapshot_duration_ms": 10.0,
                    "open_orders": [{"client_order_id": "btc-sl", "status": "NEW"}],
                }
            def query_testnet_order(self, symbol, client_id):
                return {"found": True, "client_order_id": client_id, "status": "FILLED"}

        result = reconcile_testnet_restart(RestartAdapter(), "BTCUSDT", ["btc-sl"])
        self.assertFalse(result["ok"])
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(result["reason"], "ORDER_QUERY_SNAPSHOT_MISMATCH")


if __name__ == "__main__":
    unittest.main()
