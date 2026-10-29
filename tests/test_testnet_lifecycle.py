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
            "orig_quantity": str(abs(DecimalPosition(self.position))), "executed_quantity": "0",
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
            "stop_price": "99", "side": "SELL", "orig_quantity": "0.5", "executed_quantity": "0",
            "client_order_id": "btc-run-03-sl",
        }])
        result = ensure_testnet_protective_exits(adapter, "BTCUSDT", "99", "102", "btc-run-03")
        self.assertTrue(result["ok"])
        self.assertEqual([s[1] for s in adapter.submissions], ["take_profit"])

    def test_restart_with_both_matching_protective_orders_is_idempotent(self):
        adapter = FakeAdapter(orders=[
            {
                "status": "NEW", "reduce_only": True, "type": "STOP_MARKET",
                "stop_price": "99", "side": "SELL", "orig_quantity": "0.5",
                "executed_quantity": "0", "client_order_id": "btc-restart-sl",
            },
            {
                "status": "NEW", "reduce_only": True, "type": "TAKE_PROFIT_MARKET",
                "stop_price": "102", "side": "SELL", "orig_quantity": "0.5",
                "executed_quantity": "0", "client_order_id": "btc-restart-tp",
            },
        ])
        result = ensure_testnet_protective_exits(
            adapter, "BTCUSDT", "99", "102", "btc-restart"
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["mode"], "testnet_protective_exits_verified")
        self.assertEqual(adapter.submissions, [])
        self.assertEqual(adapter.snapshot_count, 2)
        self.assertEqual(
            set(result["verified_orders"]),
            {"STOP_MARKET", "TAKE_PROFIT_MARKET"},
        )

    def test_restart_with_duplicate_client_id_conflict_does_not_submit_again(self):
        class ConflictAdapter(FakeAdapter):
            def submit_testnet_protective_exit(self, symbol, kind, quantity, price, client_id):
                self.submissions.append((symbol, kind, str(quantity), str(price), client_id))
                return {
                    "accepted": False,
                    "reason": "protective_exit_client_id_conflict_requires_reconciliation",
                    "requires_reconciliation": True,
                    "client_order_id": client_id,
                }

        adapter = ConflictAdapter()
        result = ensure_testnet_protective_exits(
            adapter, "BTCUSDT", "99", "102", "btc-conflict"
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "stop_submission_failed")
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual([item[1] for item in adapter.submissions], ["stop_loss"])
        self.assertEqual(adapter.snapshot_count, 1)

    def test_existing_stop_with_unrelated_client_id_fails_closed(self):
        adapter = FakeAdapter(orders=[{
            "status": "NEW", "reduce_only": True, "type": "STOP_MARKET",
            "stop_price": "99", "side": "SELL", "orig_quantity": "0.5", "executed_quantity": "0",
            "client_order_id": "unrelated-stop-id",
        }])
        result = ensure_testnet_protective_exits(adapter, "BTCUSDT", "99", "102", "btc-run-04a")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "existing_stop_client_order_id_mismatch")
        self.assertEqual(adapter.submissions, [])

    def test_mismatched_existing_stop_fails_closed(self):
        adapter = FakeAdapter(orders=[{
            "status": "NEW", "reduce_only": True, "type": "STOP_MARKET",
            "stop_price": "98", "side": "SELL", "orig_quantity": "0.5", "executed_quantity": "0",
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

    def test_position_sign_reversal_during_setup_fails_closed_even_same_abs_quantity(self):
        class ReversedPositionAdapter(FakeAdapter):
            def testnet_position_snapshot(self, symbol):
                result = super().testnet_position_snapshot(symbol)
                if self.snapshot_count > 1:
                    result["position_amount"] = "-0.5"
                return result

        adapter = ReversedPositionAdapter()
        result = ensure_testnet_protective_exits(
            adapter, "BTCUSDT", "99", "102", "btc-run-reversed"
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "position_changed_during_protection_setup")
        self.assertTrue(result["requires_reconciliation"])

    def test_entry_price_change_during_setup_fails_closed(self):
        class EntryPriceChangedAdapter(FakeAdapter):
            def testnet_position_snapshot(self, symbol):
                result = super().testnet_position_snapshot(symbol)
                if self.snapshot_count > 1:
                    result["entry_price"] = "100.1"
                return result

        adapter = EntryPriceChangedAdapter()
        result = ensure_testnet_protective_exits(
            adapter, "BTCUSDT", "99", "102", "btc-run-entry-drift"
        )
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
        self.assertEqual(result["reason"], "existing_stop_quantity_or_execution_mismatch")
        self.assertEqual(adapter.submissions, [])


    def test_sibling_lookup_exception_fails_closed_without_cancel(self):
        class SiblingLookupRaisesAdapter:
            def __init__(self):
                self.cancel_calls = 0
                self.query_calls = 0

            def query_testnet_order(self, symbol, client_id):
                self.query_calls += 1
                if client_id == "btc-sl":
                    return {
                        "found": True, "symbol": symbol, "client_order_id": client_id,
                        "status": "FILLED", "executed_quantity": "0.5",
                        "original_quantity": "0.5",
                    }
                raise TimeoutError("sibling order lookup unavailable")

            def testnet_position_snapshot(self, symbol):
                return {
                    "ok": True, "symbol": symbol, "position_amount": "0",
                    "open_orders": [],
                }

            def cancel_testnet_order(self, symbol, client_id):
                self.cancel_calls += 1
                return {"cancelled": True}

        adapter = SiblingLookupRaisesAdapter()
        result = cancel_testnet_sibling_after_flatten(
            adapter, "BTCUSDT", "btc-sl", "btc-tp"
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "sibling_lookup_exception")
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(adapter.query_calls, 2)
        self.assertEqual(adapter.cancel_calls, 0)

    def test_sibling_cancel_exception_is_ambiguous_and_not_retried(self):
        class CancelRaisesAdapter:
            def __init__(self):
                self.cancel_calls = 0

            def query_testnet_order(self, symbol, client_id):
                status = "FILLED" if client_id == "btc-sl" else "NEW"
                executed = "0.5" if status == "FILLED" else "0"
                return {"found": True, "symbol": symbol, "client_order_id": client_id, "status": status, "executed_quantity": executed, "original_quantity": "0.5"}

            def testnet_position_snapshot(self, symbol):
                return {"ok": True, "symbol": symbol, "position_amount": "0", "open_orders": []}

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
                return {"ok": True, "symbol": symbol, "position_amount": "0", "open_orders": []}

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
                    executed = "0.5" if status == "FILLED" else "0.2"
                else:
                    status = "CANCELED" if self.cancelled else self.sibling_status
                    executed = "0" if status in {"NEW", "CANCELED"} else "0.2" if status == "PARTIALLY_FILLED" else "0.5"
                return {"found": True, "symbol": symbol, "client_order_id": client_id, "status": status, "executed_quantity": executed, "original_quantity": "0.5"}

            def testnet_position_snapshot(self, symbol):
                return {"ok": True, "symbol": symbol, "position_amount": self.position, "open_orders": []}

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
                return {"found": True, "symbol": symbol, "client_order_id": client_id, "status": "FILLED", "executed_quantity": "0.5", "original_quantity": "0.5"}

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

    def test_partially_filled_sibling_is_not_cancelled_even_if_position_snapshot_is_flat(self):
        class PartiallyFilledSiblingAdapter:
            def __init__(self):
                self.cancel_calls = 0

            def query_testnet_order(self, symbol, client_id):
                if client_id == "btc-sl":
                    return {
                        "found": True, "symbol": symbol, "client_order_id": client_id,
                        "status": "FILLED", "executed_quantity": "0.5",
                        "original_quantity": "0.5",
                    }
                return {
                    "found": True, "symbol": symbol, "client_order_id": client_id,
                    "status": "PARTIALLY_FILLED", "executed_quantity": "0.1",
                    "original_quantity": "0.5",
                }

            def testnet_position_snapshot(self, symbol):
                return {
                    "ok": True, "symbol": symbol, "position_amount": "0",
                    "open_orders": [],
                }

            def cancel_testnet_order(self, symbol, client_id):
                self.cancel_calls += 1
                return {"cancelled": True}

        adapter = PartiallyFilledSiblingAdapter()
        result = cancel_testnet_sibling_after_flatten(
            adapter, "BTCUSDT", "btc-sl", "btc-tp"
        )
        self.assertFalse(result["ok"])
        self.assertEqual(
            result["reason"], "sibling_partial_fill_status_requires_reconciliation"
        )
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(adapter.cancel_calls, 0)

    def test_active_sibling_with_missing_execution_quantity_fails_closed(self):
        class MissingSiblingExecutionAdapter:
            def __init__(self):
                self.cancel_calls = 0

            def query_testnet_order(self, symbol, client_id):
                result = {
                    "found": True, "symbol": symbol, "client_order_id": client_id,
                    "status": "FILLED" if client_id == "btc-sl" else "NEW",
                    "original_quantity": "0.5",
                }
                if client_id == "btc-sl":
                    result["executed_quantity"] = "0.5"
                return result

            def testnet_position_snapshot(self, symbol):
                return {
                    "ok": True, "symbol": symbol, "position_amount": "0",
                    "open_orders": [],
                }

            def cancel_testnet_order(self, symbol, client_id):
                self.cancel_calls += 1
                return {"cancelled": True}

        adapter = MissingSiblingExecutionAdapter()
        result = cancel_testnet_sibling_after_flatten(
            adapter, "BTCUSDT", "btc-sl", "btc-tp"
        )
        self.assertFalse(result["ok"])
        self.assertEqual(
            result["reason"], "sibling_execution_state_requires_reconciliation"
        )
        self.assertEqual(adapter.cancel_calls, 0)

    def test_sibling_is_not_cancelled_on_partial_fill_or_nonflat_position(self):
        class ReconcileAdapter:
            def __init__(self, status, position):
                self.status = status
                self.position = position
                self.cancel_calls = []

            def query_testnet_order(self, symbol, client_id):
                executed = "0.5" if self.status == "FILLED" else "0.2"
                return {"found": True, "symbol": symbol, "client_order_id": client_id, "status": self.status, "executed_quantity": executed, "original_quantity": "0.5"}

            def testnet_position_snapshot(self, symbol):
                return {"ok": True, "symbol": symbol, "position_amount": self.position, "open_orders": []}

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
                        "status": "FILLED", "executed_quantity": "0.5", "original_quantity": "0.5",
                    }
                return {
                    "found": True, "symbol": symbol, "client_order_id": client_id,
                    "status": "UNKNOWN", "executed_quantity": "0", "original_quantity": "0.5",
                }

            def testnet_position_snapshot(self, symbol):
                return {"ok": True, "symbol": symbol, "position_amount": "0", "open_orders": []}

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
                "stop_price": "99", "side": "SELL", "orig_quantity": "0.5", "executed_quantity": "0",
                "client_order_id": "btc-restart-01-sl",
            },
            {
                "status": "NEW", "reduce_only": True, "type": "TAKE_PROFIT_MARKET",
                "stop_price": "102", "side": "SELL", "orig_quantity": "0.5", "executed_quantity": "0",
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
                "stop_price": "99", "side": "SELL", "orig_quantity": "0.5", "executed_quantity": "0",
                "client_order_id": "btc-restart-02-sl",
            },
            {
                "status": "NEW", "reduce_only": True, "type": "STOP_MARKET",
                "stop_price": "99", "side": "SELL", "orig_quantity": "0.5", "executed_quantity": "0",
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
                return {"ok": True, "symbol": symbol, "position_amount": "0", "open_orders": []}

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
                return {"ok": True, "symbol": symbol, "position_amount": "0", "open_orders": []}

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


    def test_sibling_cleanup_fails_if_open_orders_remain_after_flatten(self):
        class RemainingOrderAdapter:
            def __init__(self):
                self.snapshot_calls = 0
                self.cancelled = False

            def query_testnet_order(self, symbol, client_id):
                if client_id == "btc-sl":
                    return {
                        "found": True, "symbol": symbol, "client_order_id": client_id,
                        "status": "FILLED", "executed_quantity": "0.5",
                        "original_quantity": "0.5",
                    }
                status = "CANCELED" if self.cancelled else "NEW"
                return {
                    "found": True, "symbol": symbol, "client_order_id": client_id,
                    "status": status, "executed_quantity": "0",
                    "original_quantity": "0.5",
                }

            def testnet_position_snapshot(self, symbol):
                self.snapshot_calls += 1
                if self.snapshot_calls == 1:
                    return {"ok": True, "symbol": symbol, "position_amount": "0", "open_orders": []}
                return {
                    "ok": True, "symbol": symbol, "position_amount": "0",
                    "open_orders": [{
                        "symbol": symbol, "client_order_id": "unexpected-entry",
                        "status": "NEW", "type": "LIMIT", "reduce_only": False,
                    }],
                }

            def cancel_testnet_order(self, symbol, client_id):
                self.cancelled = True
                return {"cancelled": True}

        adapter = RemainingOrderAdapter()
        result = cancel_testnet_sibling_after_flatten(adapter, "BTCUSDT", "btc-sl", "btc-tp")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "open_orders_remain_after_flatten")
        self.assertTrue(result["requires_reconciliation"])


    def test_post_cancel_sibling_identity_mismatch_fails_closed(self):
        class PostCancelIdentityMismatchAdapter:
            def __init__(self):
                self.sibling_queries = 0
                self.cancel_calls = 0

            def query_testnet_order(self, symbol, client_id):
                if client_id == "btc-sl":
                    return {
                        "found": True, "symbol": symbol, "client_order_id": client_id,
                        "status": "FILLED", "executed_quantity": "0.5",
                        "original_quantity": "0.5",
                    }
                self.sibling_queries += 1
                if self.sibling_queries == 1:
                    return {
                        "found": True, "symbol": symbol, "client_order_id": client_id,
                        "status": "NEW", "executed_quantity": "0",
                        "original_quantity": "0.5",
                    }
                return {
                    "found": True, "symbol": "ETHUSDT", "client_order_id": client_id,
                    "status": "CANCELED", "executed_quantity": "0",
                    "original_quantity": "0.5",
                }

            def testnet_position_snapshot(self, symbol):
                return {"ok": True, "symbol": symbol, "position_amount": "0", "open_orders": []}

            def cancel_testnet_order(self, symbol, client_id):
                self.cancel_calls += 1
                return {"cancelled": True}

        adapter = PostCancelIdentityMismatchAdapter()
        result = cancel_testnet_sibling_after_flatten(adapter, "BTCUSDT", "btc-sl", "btc-tp")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "post_cancel_sibling_identity_mismatch")
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(adapter.cancel_calls, 1)


    def test_sibling_cleanup_fails_closed_when_open_orders_snapshot_missing(self):
        class MissingOpenOrdersAdapter:
            def __init__(self):
                self.snapshot_calls = 0
                self.cancel_calls = 0
                self.cancelled = False

            def query_testnet_order(self, symbol, client_id):
                if client_id == "btc-sl":
                    status, quantity = "FILLED", "0.5"
                else:
                    status = "CANCELED" if self.cancelled else "NEW"
                    quantity = "0"
                return {
                    "found": True, "symbol": symbol, "client_order_id": client_id,
                    "status": status, "executed_quantity": quantity,
                    "original_quantity": "0.5",
                }

            def testnet_position_snapshot(self, symbol):
                self.snapshot_calls += 1
                return {"ok": True, "symbol": symbol, "position_amount": "0"}

            def cancel_testnet_order(self, symbol, client_id):
                self.cancel_calls += 1
                self.cancelled = True
                return {"cancelled": True}

        adapter = MissingOpenOrdersAdapter()
        result = cancel_testnet_sibling_after_flatten(adapter, "BTCUSDT", "btc-sl", "btc-tp")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "open_orders_snapshot_missing")
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(adapter.cancel_calls, 1)

    def test_inactive_sibling_with_partial_execution_requires_reconciliation(self):
        class InactivePartialSiblingAdapter:
            def __init__(self):
                self.snapshot_calls = 0
                self.cancel_calls = 0

            def query_testnet_order(self, symbol, client_id):
                status = "FILLED" if client_id == "btc-sl" else "CANCELED"
                executed = "0.5" if status == "FILLED" else "0.1"
                return {
                    "found": True, "symbol": symbol, "client_order_id": client_id,
                    "status": status, "executed_quantity": executed,
                    "original_quantity": "0.5",
                }

            def testnet_position_snapshot(self, symbol):
                self.snapshot_calls += 1
                return {
                    "ok": True, "symbol": symbol, "position_amount": "0",
                    "open_orders": [],
                }

            def cancel_testnet_order(self, symbol, client_id):
                self.cancel_calls += 1
                return {"cancelled": True}

        adapter = InactivePartialSiblingAdapter()
        result = cancel_testnet_sibling_after_flatten(
            adapter, "BTCUSDT", "btc-sl", "btc-tp"
        )
        self.assertFalse(result["ok"])
        self.assertEqual(
            result["reason"], "inactive_sibling_execution_state_requires_reconciliation"
        )
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(adapter.snapshot_calls, 1)
        self.assertEqual(adapter.cancel_calls, 0)

    def test_already_inactive_sibling_still_checks_remaining_open_orders(self):
        class InactiveSiblingWithOtherOrdersAdapter:
            def __init__(self):
                self.snapshot_calls = 0

            def query_testnet_order(self, symbol, client_id):
                status = "FILLED" if client_id == "btc-sl" else "CANCELED"
                quantity = "0.5" if status == "FILLED" else "0"
                return {
                    "found": True, "symbol": symbol, "client_order_id": client_id,
                    "status": status, "executed_quantity": quantity,
                    "original_quantity": "0.5",
                }

            def testnet_position_snapshot(self, symbol):
                self.snapshot_calls += 1
                snapshot = {"ok": True, "symbol": symbol, "position_amount": "0", "open_orders": []}
                if self.snapshot_calls == 2:
                    snapshot["open_orders"] = [{"client_order_id": "unrelated-live-order"}]
                return snapshot

            def cancel_testnet_order(self, symbol, client_id):
                raise AssertionError("must not cancel when sibling is already inactive")

        adapter = InactiveSiblingWithOtherOrdersAdapter()
        result = cancel_testnet_sibling_after_flatten(adapter, "BTCUSDT", "btc-sl", "btc-tp")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "open_orders_remain_after_flatten")
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(adapter.snapshot_calls, 2)



    def test_duplicate_client_ids_in_initial_snapshot_fail_closed(self):
        adapter = FakeAdapter(orders=[
            {
                "status": "NEW", "reduce_only": True, "type": "STOP_MARKET",
                "stop_price": "99", "side": "SELL", "orig_quantity": "0.5",
                "executed_quantity": "0", "client_order_id": "same-protective-id",
            },
            {
                "status": "NEW", "reduce_only": True, "type": "TAKE_PROFIT_MARKET",
                "stop_price": "102", "side": "SELL", "orig_quantity": "0.5",
                "executed_quantity": "0", "client_order_id": "same-protective-id",
            },
        ])
        result = ensure_testnet_protective_exits(adapter, "BTCUSDT", "99", "102", "btc-duplicate")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "duplicate_or_invalid_open_order_client_ids_requires_reconciliation")
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(adapter.submissions, [])

    def test_both_protective_exits_filled_requires_manual_reconciliation(self):
        class BothFilledAdapter:
            def __init__(self):
                self.cancel_calls = []

            def query_testnet_order(self, symbol, client_id):
                return {
                    "found": True, "symbol": symbol, "client_order_id": client_id,
                    "status": "FILLED", "executed_quantity": "0.5", "original_quantity": "0.5",
                }

            def testnet_position_snapshot(self, symbol):
                # Even if a snapshot currently looks flat, two FILLED exits may
                # imply a race, over-close, or unexpected reverse exposure.
                return {"ok": True, "symbol": symbol, "position_amount": "0", "open_orders": []}

            def cancel_testnet_order(self, symbol, client_id):
                self.cancel_calls.append(client_id)
                return {"cancelled": True}

        adapter = BothFilledAdapter()
        result = cancel_testnet_sibling_after_flatten(adapter, "BTCUSDT", "btc-sl", "btc-tp")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "both_exits_report_filled_check_exchange_history")
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(adapter.cancel_calls, [])


    def test_existing_partially_executed_protective_order_fails_closed(self):
        adapter = FakeAdapter(orders=[{
            "status": "PARTIALLY_FILLED", "reduce_only": True, "type": "STOP_MARKET",
            "stop_price": "99", "side": "SELL", "orig_quantity": "0.5",
            "executed_quantity": "0.1", "client_order_id": "btc-partial-sl",
        }])
        result = ensure_testnet_protective_exits(
            adapter, "BTCUSDT", "99", "102", "btc-partial"
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "initial_partial_fill_status_requires_reconciliation")
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(adapter.submissions, [])

    def test_existing_partial_fill_status_with_zero_execution_fails_closed(self):
        adapter = FakeAdapter(orders=[{
            "status": "PARTIALLY_FILLED", "reduce_only": True, "type": "STOP_MARKET",
            "stop_price": "99", "side": "SELL", "orig_quantity": "0.5",
            "executed_quantity": "0", "client_order_id": "btc-partial-zero-sl",
        }])
        result = ensure_testnet_protective_exits(
            adapter, "BTCUSDT", "99", "102", "btc-partial-zero"
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "initial_partial_fill_status_requires_reconciliation")
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(adapter.submissions, [])

    def test_post_submission_partially_executed_protective_order_fails_closed(self):
        class PartialExecutionAfterSubmitAdapter(FakeAdapter):
            def testnet_position_snapshot(self, symbol):
                snapshot = super().testnet_position_snapshot(symbol)
                if self.snapshot_count >= 2 and snapshot["open_orders"]:
                    snapshot["open_orders"][0]["status"] = "PARTIALLY_FILLED"
                    snapshot["open_orders"][0]["executed_quantity"] = "0.1"
                return snapshot

        adapter = PartialExecutionAfterSubmitAdapter()
        result = ensure_testnet_protective_exits(
            adapter, "BTCUSDT", "99", "102", "btc-race-partial"
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "post_submission_partial_fill_status_requires_reconciliation")
        self.assertTrue(result["requires_reconciliation"])


    def test_post_submission_partial_fill_status_with_zero_execution_fails_closed(self):
        class ContradictoryPostSubmitAdapter(FakeAdapter):
            def testnet_position_snapshot(self, symbol):
                snapshot = super().testnet_position_snapshot(symbol)
                if self.snapshot_count >= 2 and snapshot["open_orders"]:
                    snapshot["open_orders"][0]["status"] = "PARTIALLY_FILLED"
                    snapshot["open_orders"][0]["executed_quantity"] = "0"
                return snapshot

        adapter = ContradictoryPostSubmitAdapter()
        result = ensure_testnet_protective_exits(
            adapter, "BTCUSDT", "99", "102", "btc-post-partial0"
        )
        self.assertFalse(result["ok"])
        self.assertEqual(
            result["reason"],
            "post_submission_partial_fill_status_requires_reconciliation",
        )
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual([item[1] for item in adapter.submissions], ["stop_loss", "take_profit"])

    def test_partial_filled_status_with_zero_execution_still_fails_closed(self):
        class ContradictorySiblingAdapter:
            def __init__(self):
                self.cancel_calls = 0

            def query_testnet_order(self, symbol, client_id):
                if client_id == "btc-sl":
                    return {
                        "found": True, "symbol": symbol, "client_order_id": client_id,
                        "status": "FILLED", "executed_quantity": "0.5",
                        "original_quantity": "0.5",
                    }
                return {
                    "found": True, "symbol": symbol, "client_order_id": client_id,
                    "status": "PARTIALLY_FILLED", "executed_quantity": "0",
                    "original_quantity": "0.5",
                }

            def testnet_position_snapshot(self, symbol):
                return {
                    "ok": True, "symbol": symbol, "position_amount": "0",
                    "open_orders": [],
                }

            def cancel_testnet_order(self, symbol, client_id):
                self.cancel_calls += 1
                return {"cancelled": True}

        adapter = ContradictorySiblingAdapter()
        result = cancel_testnet_sibling_after_flatten(
            adapter, "BTCUSDT", "btc-sl", "btc-tp"
        )
        self.assertFalse(result["ok"])
        self.assertEqual(
            result["reason"], "sibling_partial_fill_status_requires_reconciliation"
        )
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(adapter.cancel_calls, 0)

    def test_sibling_partial_fill_before_cancel_ack_requires_reconciliation(self):
        class PartialFillDuringCancelAdapter:
            def __init__(self):
                self.query_calls = 0
                self.cancel_calls = 0
                self.snapshot_calls = 0

            def query_testnet_order(self, symbol, client_id):
                self.query_calls += 1
                if self.query_calls == 1:
                    return {
                        "found": True, "symbol": symbol, "client_order_id": client_id,
                        "status": "FILLED", "executed_quantity": "0.5",
                        "original_quantity": "0.5",
                    }
                if self.query_calls == 2:
                    return {
                        "found": True, "symbol": symbol, "client_order_id": client_id,
                        "status": "NEW", "executed_quantity": "0",
                        "original_quantity": "0.5",
                    }
                return {
                    "found": True, "symbol": symbol, "client_order_id": client_id,
                    "status": "CANCELED", "executed_quantity": "0.1",
                    "original_quantity": "0.5",
                }

            def testnet_position_snapshot(self, symbol):
                self.snapshot_calls += 1
                return {
                    "ok": True, "symbol": symbol, "position_amount": "0",
                    "open_orders": [],
                }

            def cancel_testnet_order(self, symbol, client_id):
                self.cancel_calls += 1
                return {"cancelled": True}

        adapter = PartialFillDuringCancelAdapter()
        result = cancel_testnet_sibling_after_flatten(
            adapter, "BTCUSDT", "btc-sl", "btc-tp"
        )
        self.assertFalse(result["ok"])
        self.assertEqual(
            result["reason"], "post_cancel_sibling_execution_requires_reconciliation"
        )
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(adapter.cancel_calls, 1)
        self.assertEqual(adapter.snapshot_calls, 1)

    def test_sibling_fills_between_cancel_ack_and_post_cancel_lookup_fails_closed(self):
        class SiblingFillRaceAdapter:
            def __init__(self):
                self.sibling_queries = 0
                self.cancel_calls = 0
                self.snapshot_calls = 0

            def query_testnet_order(self, symbol, client_id):
                if client_id == "btc-sl":
                    return {
                        "found": True, "symbol": symbol, "client_order_id": client_id,
                        "status": "FILLED", "executed_quantity": "0.5",
                        "original_quantity": "0.5",
                    }
                self.sibling_queries += 1
                status = "NEW" if self.sibling_queries == 1 else "FILLED"
                executed = "0" if status == "NEW" else "0.5"
                return {
                    "found": True, "symbol": symbol, "client_order_id": client_id,
                    "status": status, "executed_quantity": executed,
                    "original_quantity": "0.5",
                }

            def testnet_position_snapshot(self, symbol):
                self.snapshot_calls += 1
                return {
                    "ok": True, "symbol": symbol, "position_amount": "0",
                    "open_orders": [],
                }

            def cancel_testnet_order(self, symbol, client_id):
                self.cancel_calls += 1
                return {"cancelled": True, "status": "CANCELED"}

        adapter = SiblingFillRaceAdapter()
        result = cancel_testnet_sibling_after_flatten(
            adapter, "BTCUSDT", "btc-sl", "btc-tp"
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "post_cancel_sibling_not_confirmed_inactive")
        self.assertEqual(result["sibling_status"], "FILLED")
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(adapter.cancel_calls, 1)
        # Do not treat a cancel acknowledgement as proof of cancellation, and
        # do not take a final "clean" snapshot after observing the fill race.
        self.assertEqual(adapter.snapshot_calls, 1)


    def test_cancel_rejects_filled_non_protective_order_before_snapshot_or_cancel(self):
        class NonProtectiveFilledOrderAdapter:
            def __init__(self):
                self.snapshot_calls = 0
                self.cancel_calls = 0

            def query_testnet_order(self, symbol, client_id):
                return {
                    "found": True, "symbol": symbol, "client_order_id": client_id,
                    "status": "FILLED", "executed_quantity": "0.5",
                    "original_quantity": "0.5", "type": "LIMIT",
                    "side": "SELL", "reduce_only": False,
                }

            def testnet_position_snapshot(self, symbol):
                self.snapshot_calls += 1
                return {"ok": True, "symbol": symbol, "position_amount": "0", "open_orders": []}

            def cancel_testnet_order(self, symbol, client_id):
                self.cancel_calls += 1
                return {"cancelled": True}

        adapter = NonProtectiveFilledOrderAdapter()
        result = cancel_testnet_sibling_after_flatten(adapter, "BTCUSDT", "btc-sl", "btc-tp")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "filled_exit_not_verified_protective_order")
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(adapter.snapshot_calls, 0)
        self.assertEqual(adapter.cancel_calls, 0)

    def test_cancel_rejects_mismatched_sibling_type_or_side_before_cancel(self):
        class MismatchedProtectivePairAdapter:
            def __init__(self):
                self.cancel_calls = 0

            def query_testnet_order(self, symbol, client_id):
                if client_id == "btc-sl":
                    return {
                        "found": True, "symbol": symbol, "client_order_id": client_id,
                        "status": "FILLED", "executed_quantity": "0.5",
                        "original_quantity": "0.5", "type": "STOP_MARKET",
                        "side": "SELL", "reduce_only": True,
                    }
                return {
                    "found": True, "symbol": symbol, "client_order_id": client_id,
                    "status": "NEW", "executed_quantity": "0",
                    "original_quantity": "0.5", "type": "TAKE_PROFIT_MARKET",
                    "side": "BUY", "reduce_only": True,
                }

            def testnet_position_snapshot(self, symbol):
                return {"ok": True, "symbol": symbol, "position_amount": "0", "open_orders": []}

            def cancel_testnet_order(self, symbol, client_id):
                self.cancel_calls += 1
                return {"cancelled": True}

        adapter = MismatchedProtectivePairAdapter()
        result = cancel_testnet_sibling_after_flatten(adapter, "BTCUSDT", "btc-sl", "btc-tp")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "sibling_not_verified_as_matching_protective_pair")
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(adapter.cancel_calls, 0)


if __name__ == "__main__":
    unittest.main()
