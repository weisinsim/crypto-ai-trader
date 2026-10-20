import sys
from pathlib import Path
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from execution.runtime_state_store import RuntimeStateStore
from execution.safety import ExecutionConfig, OrderIntent
from execution.testnet_supervisor import TestnetSupervisor


class FakeAdapter:
    def __init__(self, position_amount="0", fail_metrics=False):
        self.position_amount = position_amount
        self.fail_metrics = fail_metrics
        self.submissions = []
        self.orders = []
        self.fail_position_snapshot = False
        self.position_snapshot_calls = 0

    def account_preflight(self):
        return {"ok": True, "can_trade": True}

    def account_risk_metrics(self, symbol):
        if self.fail_metrics:
            return {"ok": False, "reason": "fixture_failure"}
        count = int(float(self.position_amount) != 0)
        notional = abs(float(self.position_amount) * 100)
        return {"ok": True, "open_positions": count,
                "symbol_notional_usdt": notional, "total_notional_usdt": notional}

    def testnet_position_snapshot(self, symbol):
        self.position_snapshot_calls += 1
        if self.fail_position_snapshot:
            raise TimeoutError("position snapshot timeout")
        return {"ok": True, "position_amount": self.position_amount,
                "entry_price": "100" if self.position_amount != "0" else "0",
                "open_orders": list(self.orders)}

    def submit_testnet_market_order(self, intent, client_order_id, config):
        self.submissions.append(client_order_id)
        self.position_amount = "0.5"
        return {"accepted": True}

    def query_testnet_order(self, symbol, client_order_id):
        return {"found": True, "status": "FILLED", "executed_quantity": "0.5", "average_price": "100"}

    def submit_testnet_protective_exit(self, symbol, kind, quantity, price, client_id):
        self.orders.append({"status":"NEW", "reduce_only":True,
            "type":"STOP_MARKET" if kind=="stop_loss" else "TAKE_PROFIT_MARKET",
            "stop_price":str(price), "client_order_id":client_id,
            "side":"SELL", "orig_quantity":"0.5"})
        return {"accepted": True}


class TestnetSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = RuntimeStateStore(str(Path(self.temp.name) / "risk.sqlite"))
        self.adapter = FakeAdapter()
        self.supervisor = TestnetSupervisor(self.adapter, self.store,
            execution_config=ExecutionConfig(mode="testnet", max_notional_usdt=100,
                                             max_risk_fraction=0.005))

    def tearDown(self):
        self.temp.cleanup()

    def intent(self):
        return OrderIntent("BTCUSDT", "BUY", 100, 99.8, 100.6, 0.5, 1000, 1001, 1)

    def test_monitor_only_refreshes_persisted_exchange_metrics(self):
        result = self.supervisor.run_once("BTCUSDT")
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["entry_submitted"])
        self.assertEqual(self.store.load_snapshot().open_positions, 0)

    def test_missing_or_failed_metrics_never_submits(self):
        self.adapter.fail_metrics = True
        result = self.supervisor.run_once("BTCUSDT", self.intent(), "entry-1", "run-1")
        self.assertFalse(result["ok"])
        self.assertEqual(self.adapter.submissions, [])

    def test_existing_position_without_recovery_levels_fails_closed(self):
        self.adapter.position_amount = "0.5"
        result = self.supervisor.run_once("BTCUSDT")
        self.assertEqual(result["reason"], "existing_position_requires_protection_metadata")
        self.assertFalse(result["entry_submitted"])

    def test_intent_entry_is_guarded_and_protected(self):
        result = self.supervisor.run_once("BTCUSDT", self.intent(), "entry-1", "run-1")
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.adapter.submissions, ["entry-1"])
        self.assertFalse(result["live_trading_enabled"])



    def test_entry_lock_is_released_after_confirmed_entry(self):
        result = self.supervisor.run_once("BTCUSDT", self.intent(), "entry-lock-ok", "run-lock")
        self.assertTrue(result["ok"], result)
        self.assertIsNone(self.store.get_entry_lock())

    def test_existing_lock_blocks_new_entry_and_fails_closed(self):
        self.assertTrue(self.store.claim_entry_lock("stale-process"))
        result = self.supervisor.run_once("BTCUSDT", self.intent(), "entry-lock-blocked", "run-lock")
        self.assertEqual(result["reason"], "entry_lock_held")
        self.assertEqual(self.adapter.submissions, [])

    def test_reused_client_order_id_is_blocked_across_calls(self):
        first = self.supervisor.run_once("BTCUSDT", self.intent(), "entry-duplicate", "run-2")
        self.assertTrue(first["ok"], first)
        self.adapter.position_amount = "0"  # Simulate a reconciled flat state before accidental replay.
        second = self.supervisor.run_once("BTCUSDT", self.intent(), "entry-duplicate", "run-2")
        self.assertEqual(second["reason"], "client_order_id_already_reserved")
        self.assertEqual(self.adapter.submissions, ["entry-duplicate"])
        self.assertFalse(second["requires_reconciliation"])  # Prior attempt was confirmed and account is reconciled flat.

    def test_emergency_stop_blocks_entry(self):
        self.store.set_emergency_stop(True)
        result = self.supervisor.run_once("BTCUSDT", self.intent(), "entry-1", "run-1")
        self.assertEqual(result["reason"], "emergency_stop_active")
        self.assertEqual(self.adapter.submissions, [])


    def test_restart_preserves_stale_entry_lock_and_blocks_submission(self):
        self.assertTrue(self.store.claim_entry_lock("crashed-process"))
        restarted_store = RuntimeStateStore(str(Path(self.temp.name) / "risk.sqlite"))
        restarted_supervisor = TestnetSupervisor(
            self.adapter, restarted_store,
            execution_config=ExecutionConfig(mode="testnet", max_notional_usdt=100,
                                             max_risk_fraction=0.005))
        result = restarted_supervisor.run_once(
            "BTCUSDT", self.intent(), "entry-after-restart", "run-restart")
        self.assertEqual(result["reason"], "entry_lock_held")
        self.assertEqual(self.adapter.submissions, [])
        self.assertEqual(restarted_store.get_entry_lock()["token"], "crashed-process")

    def test_restart_with_unresolved_attempt_never_replays_order_id(self):
        fingerprint = "a" * 64
        self.assertTrue(self.store.claim_order_attempt("entry-unresolved", fingerprint))
        self.assertTrue(self.store.update_order_attempt("entry-unresolved", "RECONCILIATION_REQUIRED"))
        restarted_store = RuntimeStateStore(str(Path(self.temp.name) / "risk.sqlite"))
        restarted_supervisor = TestnetSupervisor(
            self.adapter, restarted_store,
            execution_config=ExecutionConfig(mode="testnet", max_notional_usdt=100,
                                             max_risk_fraction=0.005))
        result = restarted_supervisor.run_once(
            "BTCUSDT", self.intent(), "entry-unresolved", "run-restart")
        self.assertEqual(result["reason"], "client_order_id_already_reserved")
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(self.adapter.submissions, [])
        self.assertIsNotNone(restarted_store.get_entry_lock())


    def test_position_snapshot_timeout_fails_closed_before_entry(self):
        self.adapter.fail_position_snapshot = True
        result = self.supervisor.run_once(
            "BTCUSDT", self.intent(), "entry-snapshot-timeout", "run-snapshot-timeout")
        self.assertEqual(result["reason"], "position_snapshot_unavailable")
        self.assertTrue(result["requires_manual_reconciliation"])
        self.assertFalse(result["entry_submitted"])
        self.assertEqual(self.adapter.submissions, [])
        self.assertIsNone(self.store.get_entry_lock())

    def test_under_lock_position_recheck_timeout_keeps_lock(self):
        # First snapshot is flat; the second, under-lock authoritative check times out.
        original = self.adapter.testnet_position_snapshot
        calls = {"n": 0}
        def fail_on_second(symbol):
            calls["n"] += 1
            if calls["n"] == 2:
                raise TimeoutError("under-lock position snapshot timeout")
            return original(symbol)
        self.adapter.testnet_position_snapshot = fail_on_second
        result = self.supervisor.run_once(
            "BTCUSDT", self.intent(), "entry-under-lock-timeout", "run-under-lock-timeout")
        self.assertEqual(result["reason"], "position_recheck_failed_under_lock")
        self.assertTrue(result["requires_reconciliation"])
        self.assertFalse(result["entry_submitted"])
        self.assertEqual(self.adapter.submissions, [])
        self.assertEqual(self.store.get_entry_lock()["token"], "entry-under-lock-timeout")

if __name__ == "__main__":
    unittest.main()
