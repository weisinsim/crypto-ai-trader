import unittest

from execution.testnet_lifecycle import cancel_testnet_sibling_after_flatten, ensure_testnet_protective_exits


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

    def test_ambiguous_stop_submission_propagates_reconciliation_requirement(self):
        class AmbiguousAdapter(FakeAdapter):
            def submit_testnet_protective_exit(self, symbol, kind, quantity, price, client_id):
                self.submissions.append((symbol, kind, str(quantity), str(price), client_id))
                return {
                    "accepted": False,
                    "reason": "protective_exit_outcome_unknown",
                    "requires_reconciliation": True,
                }

        adapter = AmbiguousAdapter()
        result = ensure_testnet_protective_exits(adapter, "BTCUSDT", "99", "102", "btc-run-ambiguous")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "stop_submission_failed")
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual([s[1] for s in adapter.submissions], ["stop_loss"])

    def test_target_failure_leaves_stop_and_demands_reconciliation(self):
        adapter = FakeAdapter(fail_kind="take_profit")
        result = ensure_testnet_protective_exits(adapter, "BTCUSDT", "99", "102", "btc-run-02")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "take_profit_submission_failed_stop_remains")
        self.assertEqual([s[1] for s in adapter.submissions], ["stop_loss", "take_profit"])
        self.assertTrue(result["requires_reconciliation"])

    def test_raised_stop_submission_exception_requires_reconciliation_and_stops(self):
        class RaisingAdapter(FakeAdapter):
            def submit_testnet_protective_exit(self, symbol, kind, quantity, price, client_id):
                self.submissions.append((symbol, kind, str(quantity), str(price), client_id))
                raise TimeoutError("response path failed after request")

        adapter = RaisingAdapter()
        result = ensure_testnet_protective_exits(adapter, "BTCUSDT", "99", "102", "btc-run-raise")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "stop_submission_exception_outcome_unknown")
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual([s[1] for s in adapter.submissions], ["stop_loss"])

    def test_raised_target_submission_exception_preserves_stop_and_requires_reconciliation(self):
        class RaisingTargetAdapter(FakeAdapter):
            def submit_testnet_protective_exit(self, symbol, kind, quantity, price, client_id):
                if kind == "take_profit":
                    self.submissions.append((symbol, kind, str(quantity), str(price), client_id))
                    raise TimeoutError("response path failed after request")
                return super().submit_testnet_protective_exit(symbol, kind, quantity, price, client_id)

        adapter = RaisingTargetAdapter()
        result = ensure_testnet_protective_exits(adapter, "BTCUSDT", "99", "102", "btc-run-target-raise")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "take_profit_submission_exception_outcome_unknown_stop_remains")
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual([s[1] for s in adapter.submissions], ["stop_loss", "take_profit"])

    def test_existing_matching_stop_is_reused(self):
        adapter = FakeAdapter(orders=[{
            "status": "NEW", "reduce_only": True, "type": "STOP_MARKET",
            "stop_price": "99", "side": "SELL", "orig_quantity": "0.5",
            "client_order_id": "btc-run-03-sl",
        }])
        result = ensure_testnet_protective_exits(adapter, "BTCUSDT", "99", "102", "btc-run-03")
        self.assertTrue(result["ok"])
        self.assertEqual([s[1] for s in adapter.submissions], ["take_profit"])

    def test_existing_stop_with_unrelated_client_id_fails_closed(self):
        adapter = FakeAdapter(orders=[{
            "status": "NEW", "reduce_only": True, "type": "STOP_MARKET",
            "stop_price": "99", "side": "SELL", "orig_quantity": "0.5",
            "client_order_id": "unrelated-stop-id",
        }])
        result = ensure_testnet_protective_exits(adapter, "BTCUSDT", "99", "102", "btc-run-04a")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "existing_stop_client_order_id_mismatch")
        self.assertEqual(adapter.submissions, [])

    def test_mismatched_existing_stop_fails_closed(self):
        adapter = FakeAdapter(orders=[{
            "status": "NEW", "reduce_only": True, "type": "STOP_MARKET",
            "stop_price": "98", "side": "SELL", "orig_quantity": "0.5",
            "client_order_id": "btc-run-04-sl",
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

    def test_initial_snapshot_for_wrong_symbol_fails_closed(self):
        class WrongSymbolAdapter(FakeAdapter):
            def testnet_position_snapshot(self, symbol):
                result = super().testnet_position_snapshot(symbol)
                result["symbol"] = "ETHUSDT"
                return result

        adapter = WrongSymbolAdapter()
        result = ensure_testnet_protective_exits(adapter, "BTCUSDT", "99", "102", "btc-run-wrong-symbol")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "position_snapshot_symbol_mismatch")
        self.assertEqual(adapter.submissions, [])

    def test_post_submission_snapshot_for_wrong_symbol_requires_reconciliation(self):
        class WrongFinalSymbolAdapter(FakeAdapter):
            def testnet_position_snapshot(self, symbol):
                result = super().testnet_position_snapshot(symbol)
                if self.snapshot_count > 1:
                    result["symbol"] = "ETHUSDT"
                return result

        adapter = WrongFinalSymbolAdapter()
        result = ensure_testnet_protective_exits(adapter, "BTCUSDT", "99", "102", "btc-run-wrong-final")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "post_submission_snapshot_symbol_mismatch")
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual([s[1] for s in adapter.submissions], ["stop_loss", "take_profit"])

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
            "client_order_id": "btc-run-08-sl",
        }])
        result = ensure_testnet_protective_exits(adapter, "BTCUSDT", "99", "102", "btc-run-08")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "existing_stop_quantity_mismatch")
        self.assertEqual(adapter.submissions, [])


    def test_sibling_cancel_exception_is_ambiguous_and_not_retried(self):
        class CancelRaisesAdapter:
            def __init__(self):
                self.cancel_calls = 0

            def query_testnet_order(self, symbol, client_id):
                status = "FILLED" if client_id == "btc-sl" else "NEW"
                return {"found": True, "status": status, "executed_quantity": "0.5"}

            def testnet_position_snapshot(self, symbol):
                return {"ok": True, "symbol": symbol, "position_amount": "0"}

            def cancel_testnet_order(self, symbol, client_id):
                self.cancel_calls += 1
                raise TimeoutError("cancel response lost")

        adapter = CancelRaisesAdapter()
        result = cancel_testnet_sibling_after_flatten(adapter, "BTCUSDT", "btc-sl", "btc-tp")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "sibling_cancel_exception_outcome_unknown")
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(adapter.cancel_calls, 1)

    def test_filled_exit_lookup_exception_fails_closed_before_snapshot_or_cancel(self):
        class LookupRaisesAdapter:
            def __init__(self):
                self.snapshot_calls = 0
                self.cancel_calls = 0

            def query_testnet_order(self, symbol, client_id):
                raise TimeoutError("order query failed")

            def testnet_position_snapshot(self, symbol):
                self.snapshot_calls += 1
                return {"ok": True, "symbol": symbol, "position_amount": "0"}

            def cancel_testnet_order(self, symbol, client_id):
                self.cancel_calls += 1
                return {"cancelled": True}

        adapter = LookupRaisesAdapter()
        result = cancel_testnet_sibling_after_flatten(adapter, "BTCUSDT", "btc-sl", "btc-tp")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "filled_exit_lookup_exception")
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(adapter.snapshot_calls, 0)
        self.assertEqual(adapter.cancel_calls, 0)

    def test_sibling_cancel_requires_filled_exit_and_flat_position(self):
        class ReconcileAdapter:
            def __init__(self, status="FILLED", position="0", sibling_status="NEW", cancel_ok=True):
                self.status = status
                self.position = position
                self.sibling_status = sibling_status
                self.cancel_ok = cancel_ok
                self.cancelled = False
                self.cancel_calls = []

            def query_testnet_order(self, symbol, client_id):
                if client_id == "btc-sl":
                    status = self.status
                else:
                    status = "CANCELED" if self.cancelled else self.sibling_status
                return {"found": True, "status": status, "executed_quantity": "0.5"}

            def testnet_position_snapshot(self, symbol):
                return {"ok": True, "symbol": symbol, "position_amount": self.position}

            def cancel_testnet_order(self, symbol, client_id):
                self.cancel_calls.append((symbol, client_id))
                self.cancelled = self.cancel_ok
                return {"cancelled": self.cancel_ok, "status": "CANCELED" if self.cancel_ok else "UNKNOWN"}

        adapter = ReconcileAdapter()
        result = cancel_testnet_sibling_after_flatten(adapter, "BTCUSDT", "btc-sl", "btc-tp")
        self.assertTrue(result["ok"])
        self.assertEqual(adapter.cancel_calls, [("BTCUSDT", "btc-tp")])

    def test_sibling_cancel_rejects_wrong_symbol_snapshot(self):
        class WrongSymbolAdapter:
            def __init__(self):
                self.cancel_calls = []

            def query_testnet_order(self, symbol, client_id):
                return {"found": True, "status": "FILLED", "executed_quantity": "0.5"}

            def testnet_position_snapshot(self, symbol):
                return {"ok": True, "symbol": "ETHUSDT", "position_amount": "0"}

            def cancel_testnet_order(self, symbol, client_id):
                self.cancel_calls.append(client_id)
                return {"cancelled": True}

        adapter = WrongSymbolAdapter()
        result = cancel_testnet_sibling_after_flatten(adapter, "BTCUSDT", "btc-sl", "btc-tp")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "position_snapshot_symbol_mismatch")
        self.assertEqual(adapter.cancel_calls, [])

    def test_sibling_is_not_cancelled_on_partial_fill_or_nonflat_position(self):
        class ReconcileAdapter:
            def __init__(self, status, position):
                self.status = status
                self.position = position
                self.cancel_calls = []

            def query_testnet_order(self, symbol, client_id):
                return {"found": True, "status": self.status, "executed_quantity": "0.2"}

            def testnet_position_snapshot(self, symbol):
                return {"ok": True, "symbol": symbol, "position_amount": self.position}

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


    def test_unknown_sibling_order_status_fails_closed_without_cancel(self):
        class UnknownSiblingStatusAdapter:
            def __init__(self):
                self.cancel_calls = []

            def query_testnet_order(self, symbol, client_id):
                if client_id == "btc-sl":
                    return {
                        "found": True, "symbol": symbol, "client_order_id": client_id,
                        "status": "FILLED", "executed_quantity": "0.5",
                    }
                return {
                    "found": True, "symbol": symbol, "client_order_id": client_id,
                    "status": "UNKNOWN", "executed_quantity": "0",
                }

            def testnet_position_snapshot(self, symbol):
                return {"ok": True, "symbol": symbol, "position_amount": "0"}

            def cancel_testnet_order(self, symbol, client_id):
                self.cancel_calls.append((symbol, client_id))
                return {"cancelled": True}

        adapter = UnknownSiblingStatusAdapter()
        result = cancel_testnet_sibling_after_flatten(adapter, "BTCUSDT", "btc-sl", "btc-tp")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "sibling_status_unknown_requires_reconciliation")
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(adapter.cancel_calls, [])


    def test_initial_snapshot_exception_fails_closed_without_submission(self):
        class SnapshotRaisesAdapter(FakeAdapter):
            def testnet_position_snapshot(self, symbol):
                raise TimeoutError("snapshot read failed")

        adapter = SnapshotRaisesAdapter()
        result = ensure_testnet_protective_exits(adapter, "BTCUSDT", "99", "102", "btc-snap-err")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "position_snapshot_exception")
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(adapter.submissions, [])

    def test_post_submission_snapshot_exception_requires_reconciliation(self):
        class FinalSnapshotRaisesAdapter(FakeAdapter):
            def testnet_position_snapshot(self, symbol):
                if self.snapshot_count >= 1:
                    self.snapshot_count += 1
                    if self.snapshot_count > 1:
                        raise TimeoutError("post-submit snapshot read failed")
                return super().testnet_position_snapshot(symbol)

        adapter = FinalSnapshotRaisesAdapter()
        result = ensure_testnet_protective_exits(adapter, "BTCUSDT", "99", "102", "btc-final-err")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "post_submission_snapshot_exception")
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual([s[1] for s in adapter.submissions], ["stop_loss", "take_profit"])


    def test_restart_reuses_both_matching_protective_orders_without_resubmitting(self):
        adapter = FakeAdapter(orders=[
            {
                "status": "NEW", "reduce_only": True, "type": "STOP_MARKET",
                "stop_price": "99", "side": "SELL", "orig_quantity": "0.5",
                "client_order_id": "btc-restart-01-sl",
            },
            {
                "status": "NEW", "reduce_only": True, "type": "TAKE_PROFIT_MARKET",
                "stop_price": "102", "side": "SELL", "orig_quantity": "0.5",
                "client_order_id": "btc-restart-01-tp",
            },
        ])
        result = ensure_testnet_protective_exits(
            adapter, "BTCUSDT", "99", "102", "btc-restart-01"
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["mode"], "testnet_protective_exits_verified")
        self.assertEqual(adapter.submissions, [])
        self.assertEqual(len(result["verified_orders"]), 2)
        self.assertEqual(adapter.snapshot_count, 2)

    def test_restart_with_duplicate_stop_orders_fails_closed_without_submission(self):
        duplicate_stops = [
            {
                "status": "NEW", "reduce_only": True, "type": "STOP_MARKET",
                "stop_price": "99", "side": "SELL", "orig_quantity": "0.5",
                "client_order_id": "btc-restart-02-sl",
            },
            {
                "status": "NEW", "reduce_only": True, "type": "STOP_MARKET",
                "stop_price": "99", "side": "SELL", "orig_quantity": "0.5",
                "client_order_id": "btc-restart-02-alt",
            },
        ]
        adapter = FakeAdapter(orders=duplicate_stops)
        result = ensure_testnet_protective_exits(
            adapter, "BTCUSDT", "99", "102", "btc-restart-02"
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "duplicate_protective_orders_require_manual_reconciliation")
        self.assertEqual(adapter.submissions, [])


    def test_sibling_cancel_rejects_filled_order_identity_mismatch(self):
        class WrongFilledIdentityAdapter:
            def __init__(self):
                self.snapshot_calls = 0
                self.cancel_calls = 0

            def query_testnet_order(self, symbol, client_id):
                return {
                    "found": True,
                    "symbol": "ETHUSDT",
                    "client_order_id": client_id,
                    "status": "FILLED",
                    "executed_quantity": "0.5",
                    "original_quantity": "0.5",
                }

            def testnet_position_snapshot(self, symbol):
                self.snapshot_calls += 1
                return {"ok": True, "symbol": symbol, "position_amount": "0"}

            def cancel_testnet_order(self, symbol, client_id):
                self.cancel_calls += 1
                return {"cancelled": True}

        adapter = WrongFilledIdentityAdapter()
        result = cancel_testnet_sibling_after_flatten(adapter, "BTCUSDT", "btc-sl", "btc-tp")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "filled_exit_identity_mismatch")
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(adapter.snapshot_calls, 0)
        self.assertEqual(adapter.cancel_calls, 0)

    def test_sibling_cancel_rejects_filled_quantity_mismatch(self):
        class QuantityMismatchAdapter:
            def __init__(self):
                self.snapshot_calls = 0
                self.cancel_calls = 0

            def query_testnet_order(self, symbol, client_id):
                return {
                    "found": True,
                    "symbol": symbol,
                    "client_order_id": client_id,
                    "status": "FILLED",
                    "executed_quantity": "0.4",
                    "original_quantity": "0.5",
                }

            def testnet_position_snapshot(self, symbol):
                self.snapshot_calls += 1
                return {"ok": True, "symbol": symbol, "position_amount": "0"}

            def cancel_testnet_order(self, symbol, client_id):
                self.cancel_calls += 1
                return {"cancelled": True}

        adapter = QuantityMismatchAdapter()
        result = cancel_testnet_sibling_after_flatten(adapter, "BTCUSDT", "btc-sl", "btc-tp")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "invalid_or_inconsistent_filled_quantity")
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(adapter.snapshot_calls, 0)
        self.assertEqual(adapter.cancel_calls, 0)


if __name__ == "__main__":
    unittest.main()
