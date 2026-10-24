import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from execution.testnet_recovery import recover_testnet_position


class RecoveryAdapter:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.entry_calls = 0
        self.snapshot_calls = 0

    def testnet_position_snapshot(self, symbol):
        self.snapshot_calls += 1
        if isinstance(self.snapshot, Exception):
            raise self.snapshot
        return self.snapshot


class TestnetRecoveryTests(unittest.TestCase):
    def test_flat_account_does_not_submit_entry(self):
        adapter = RecoveryAdapter({"ok": True, "position_amount": "0", "entry_price": "0", "open_orders": []})
        result = recover_testnet_position(adapter, "BTCUSDT", 99, 101, "recover-01")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["mode"], "testnet_recovery_no_open_position")
        self.assertFalse(result["entry_order_submitted"])
        self.assertEqual(adapter.snapshot_calls, 1)

    def test_snapshot_failure_fails_closed(self):
        adapter = RecoveryAdapter(TimeoutError("offline"))
        result = recover_testnet_position(adapter, "BTCUSDT", 99, 101, "recover-01")
        self.assertEqual(result["reason"], "recovery_snapshot_failed")
        self.assertTrue(result["requires_manual_reconciliation"])

    def test_invalid_protective_geometry_does_not_mutate(self):
        adapter = RecoveryAdapter({"ok": True, "position_amount": "1", "entry_price": "100", "open_orders": []})
        result = recover_testnet_position(adapter, "BTCUSDT", 101, 102, "recover-01")
        self.assertEqual(result["reason"], "recovery_protective_geometry_invalid")
        self.assertEqual(adapter.snapshot_calls, 1)


    def test_non_finite_position_values_fail_closed(self):
        adapter = RecoveryAdapter({
            "ok": True, "position_amount": "NaN", "entry_price": "100", "open_orders": []
        })
        result = recover_testnet_position(adapter, "BTCUSDT", 99, 101, "recover-nan")
        self.assertEqual(result["reason"], "recovery_position_values_invalid")
        self.assertTrue(result["requires_manual_reconciliation"])
        self.assertEqual(adapter.snapshot_calls, 1)

    def test_short_position_requires_target_below_entry_below_stop(self):
        adapter = RecoveryAdapter({
            "ok": True, "position_amount": "-1", "entry_price": "100", "open_orders": []
        })
        result = recover_testnet_position(adapter, "BTCUSDT", 99, 98, "recover-short-invalid")
        self.assertEqual(result["reason"], "recovery_protective_geometry_invalid")
        self.assertTrue(result["requires_manual_reconciliation"])
        self.assertEqual(adapter.snapshot_calls, 1)


    def test_protection_exception_fails_closed_without_entry(self):
        adapter = RecoveryAdapter({
            "ok": True, "position_amount": "1", "entry_price": "100", "open_orders": []
        })
        with patch("execution.testnet_recovery.ensure_testnet_protective_exits",
                   side_effect=TimeoutError("protective order status unknown")):
            result = recover_testnet_position(adapter, "BTCUSDT", 99, 101, "recover-timeout")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "recovery_protection_check_failed")
        self.assertTrue(result["requires_manual_reconciliation"])
        self.assertFalse(result.get("entry_order_submitted", False))
        self.assertEqual(adapter.snapshot_calls, 1)

    def test_unverified_protection_fails_closed(self):
        adapter = RecoveryAdapter({
            "ok": True, "position_amount": "1", "entry_price": "100", "open_orders": []
        })
        with patch("execution.testnet_recovery.ensure_testnet_protective_exits",
                   return_value={"ok": False, "reason": "stop_missing"}):
            result = recover_testnet_position(adapter, "BTCUSDT", 99, 101, "recover-unverified")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "recovery_protection_not_verified")
        self.assertTrue(result["requires_manual_reconciliation"])
        self.assertFalse(result.get("entry_order_submitted", False))

    def test_snapshot_with_ok_false_fails_closed(self):
        adapter = RecoveryAdapter({"ok": False, "reason": "exchange_unavailable"})
        result = recover_testnet_position(adapter, "BTCUSDT", 99, 101, "recover-unavailable")
        self.assertEqual(result["reason"], "recovery_snapshot_invalid")
        self.assertTrue(result["requires_manual_reconciliation"])
        self.assertEqual(adapter.snapshot_calls, 1)

    def test_unknown_order_status_requires_manual_reconciliation(self):
        adapter = RecoveryAdapter({
            "ok": True, "position_amount": "1", "entry_price": "100",
            "open_orders": [{"status": "PENDING_UNKNOWN", "type": "STOP_MARKET",
                             "reduce_only": True}]
        })
        result = recover_testnet_position(adapter, "BTCUSDT", 99, 101, "recover-unknown-status")
        self.assertEqual(result["reason"], "recovery_order_status_unknown")
        self.assertTrue(result["requires_manual_reconciliation"])

    def test_flat_position_with_active_order_requires_manual_reconciliation(self):
        adapter = RecoveryAdapter({
            "ok": True, "position_amount": "0", "entry_price": "0",
            "open_orders": [{"status": "NEW", "type": "STOP_MARKET",
                             "reduce_only": True}]
        })
        result = recover_testnet_position(adapter, "BTCUSDT", 99, 101, "recover-orphan-order")
        self.assertEqual(result["reason"], "recovery_flat_position_has_open_orders")
        self.assertTrue(result["requires_manual_reconciliation"])

    def test_active_non_protective_order_blocks_recovery(self):
        adapter = RecoveryAdapter({
            "ok": True, "position_amount": "1", "entry_price": "100",
            "open_orders": [{"status": "NEW", "type": "LIMIT",
                             "reduce_only": False}]
        })
        result = recover_testnet_position(adapter, "BTCUSDT", 99, 101, "recover-unexpected-order")
        self.assertEqual(result["reason"], "recovery_unexpected_active_orders")
        self.assertTrue(result["requires_manual_reconciliation"])

if __name__ == "__main__":
    unittest.main()
