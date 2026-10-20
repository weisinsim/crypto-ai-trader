import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from execution.runtime_guard import RuntimeSnapshot
from execution.safety import ExecutionConfig, OrderIntent
from execution.testnet_entry_flow import execute_guarded_testnet_entry


class FakeAdapter:
    def __init__(self, submit_error=False, protection_failure=False):
        self.submit_error = submit_error
        self.protection_failure = protection_failure
        self.market_submissions = []
        self.protective_submissions = []
        self.orders = []

    def account_preflight(self):
        return {"ok": True, "can_trade": True}

    def submit_testnet_market_order(self, intent, client_order_id, config):
        self.market_submissions.append(client_order_id)
        if self.submit_error:
            raise TimeoutError("simulated ambiguous timeout")
        return {"accepted": True, "status": "FILLED", "client_order_id": client_order_id}

    def query_testnet_order(self, symbol, client_order_id):
        if self.submit_error:
            return {"found": False, "reason": "order_not_found"}
        return {"found": True, "status": "FILLED", "executed_quantity": "0.5", "average_price": "100"}

    def testnet_position_snapshot(self, symbol):
        return {
            "ok": True, "position_amount": "0" if self.submit_error else "0.5", "entry_price": "0" if self.submit_error else "100",
            "open_orders": [dict(order) for order in self.orders],
        }

    def submit_testnet_protective_exit(self, symbol, kind, quantity, price, client_id):
        self.protective_submissions.append(kind)
        if self.protection_failure and kind == "take_profit":
            return {"accepted": False, "reason": "simulated failure"}
        self.orders.append({
            "status": "NEW", "reduce_only": True,
            "type": "STOP_MARKET" if kind == "stop_loss" else "TAKE_PROFIT_MARKET",
            "stop_price": str(price), "client_order_id": client_id,
            "side": "SELL", "orig_quantity": "0.5",
        })
        return {"accepted": True, "client_order_id": client_id}


class TestnetEntryFlowTests(unittest.TestCase):
    def snapshot(self, **changes):
        values = {
            "daily_realized_pnl_usdt": 0.0,
            "open_positions": 0,
            "symbol_notional_usdt": 0.0,
            "total_notional_usdt": 0.0,
            "consecutive_execution_errors": 0,
            "emergency_stop": False,
            "snapshot_age_seconds": 1.0,
        }
        values.update(changes)
        return RuntimeSnapshot(**values)

    def intent(self, **changes):
        values = {
            "symbol": "BTCUSDT", "side": "BUY", "entry": 100.0,
            "stop_loss": 99.8, "take_profit": 100.5, "quantity": 0.5,
            "signal_timestamp": 1000, "now_timestamp": 1001, "leverage": 1,
        }
        values.update(changes)
        return OrderIntent(**values)

    def run_flow(self, adapter, snapshot=None, intent=None):
        return execute_guarded_testnet_entry(
            adapter, intent or self.intent(), "btc-entry-0001", "btc-run-01",
            snapshot or self.snapshot(),
            execution_config=ExecutionConfig(mode="testnet", max_notional_usdt=100, max_risk_fraction=0.005),
        )

    def test_runtime_kill_switch_blocks_before_any_order(self):
        adapter = FakeAdapter()
        result = self.run_flow(adapter, self.snapshot(emergency_stop=True))
        self.assertEqual(result["reason"], "emergency_stop_active")
        self.assertEqual(adapter.market_submissions, [])

    def test_intent_gate_blocks_invalid_geometry(self):
        adapter = FakeAdapter()
        result = self.run_flow(adapter, intent=self.intent(stop_loss=100.1))
        self.assertEqual(result["reason"], "invalid_long_risk_geometry")
        self.assertEqual(adapter.market_submissions, [])

    def test_success_submits_once_and_verifies_protective_exits(self):
        adapter = FakeAdapter()
        result = self.run_flow(adapter)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["mode"], "guarded_testnet_entry_with_verified_protection")
        self.assertEqual(adapter.market_submissions, ["btc-entry-0001"])
        self.assertEqual(adapter.protective_submissions, ["stop_loss", "take_profit"])
        self.assertFalse(result["live_trading_enabled"])

    def test_ambiguous_market_timeout_never_auto_retries(self):
        adapter = FakeAdapter(submit_error=True)
        result = self.run_flow(adapter)
        self.assertEqual(result["reason"], "market_order_outcome_ambiguous")
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(len(adapter.market_submissions), 1)
        self.assertEqual(adapter.protective_submissions, [])

    def test_protection_failure_surfaces_open_exposure_for_reconciliation(self):
        adapter = FakeAdapter(protection_failure=True)
        result = self.run_flow(adapter)
        self.assertEqual(result["reason"], "position_open_but_protective_exits_not_verified")
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(adapter.market_submissions, ["btc-entry-0001"])


if __name__ == "__main__":
    unittest.main()


    def test_position_quantity_must_match_reconciled_order_fill(self):
        class MismatchedFillAdapter(FakeAdapter):
            def testnet_position_snapshot(self, symbol):
                snapshot = super().testnet_position_snapshot(symbol)
                snapshot["position_amount"] = "0.4"
                return snapshot

        adapter = MismatchedFillAdapter()
        result = self.run_flow(adapter)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "order_fill_and_position_quantity_mismatch")
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(adapter.protective_submissions, [])

    def test_open_position_with_zero_executed_quantity_fails_closed(self):
        class ZeroFillAdapter(FakeAdapter):
            def query_testnet_order(self, symbol, client_order_id):
                return {"found": True, "status": "FILLED", "executed_quantity": "0", "average_price": "100"}

        adapter = ZeroFillAdapter()
        result = self.run_flow(adapter)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "order_fill_and_position_quantity_mismatch")
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(adapter.protective_submissions, [])
