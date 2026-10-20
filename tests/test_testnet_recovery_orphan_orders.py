import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from execution.testnet_recovery import recover_testnet_position


class Adapter:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def testnet_position_snapshot(self, symbol):
        return self.snapshot


class RecoveryOrphanOrderTests(unittest.TestCase):
    def test_flat_position_with_active_order_requires_manual_reconciliation(self):
        adapter = Adapter({
            "ok": True, "position_amount": "0", "entry_price": "0",
            "open_orders": [{"status": "NEW", "type": "STOP_MARKET"}],
        })
        result = recover_testnet_position(adapter, "BTCUSDT", 99, 101, "recover-01")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "recovery_flat_position_has_open_orders")
        self.assertTrue(result["requires_manual_reconciliation"])
        self.assertFalse(result.get("entry_order_submitted", False))

    def test_flat_position_with_malformed_open_orders_fails_closed(self):
        adapter = Adapter({"ok": True, "position_amount": "0", "entry_price": "0", "open_orders": None})
        result = recover_testnet_position(adapter, "BTCUSDT", 99, 101, "recover-02")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "recovery_open_orders_invalid")
        self.assertTrue(result["requires_manual_reconciliation"])

    def test_open_position_with_active_non_protective_order_fails_closed(self):
        adapter = Adapter({
            "ok": True, "position_amount": "1", "entry_price": "100",
            "open_orders": [{"status": "NEW", "type": "LIMIT", "reduce_only": False}],
        })
        result = recover_testnet_position(adapter, "BTCUSDT", 99, 101, "recover-03")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "recovery_unexpected_active_orders")
        self.assertTrue(result["requires_manual_reconciliation"])

    def test_open_position_with_active_protective_orders_continues_to_verification(self):
        adapter = Adapter({
            "ok": True, "position_amount": "1", "entry_price": "100",
            "open_orders": [{"status": "NEW", "type": "STOP_MARKET", "reduce_only": True}],
        })
        with patch("execution.testnet_recovery.ensure_testnet_protective_exits", return_value={"ok": True, "verified_orders": {}}):
            result = recover_testnet_position(adapter, "BTCUSDT", 99, 101, "recover-04")
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["entry_order_submitted"])


    def test_open_position_with_unknown_order_status_fails_closed(self):
        adapter = Adapter({
            "ok": True, "position_amount": "1", "entry_price": "100",
            "open_orders": [{"status": "PENDING_CANCEL", "type": "STOP_MARKET", "reduce_only": True}],
        })
        result = recover_testnet_position(adapter, "BTCUSDT", 99, 101, "recover-05")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "recovery_order_status_unknown")
        self.assertTrue(result["requires_manual_reconciliation"])

    def test_open_position_with_missing_order_status_fails_closed(self):
        adapter = Adapter({
            "ok": True, "position_amount": "1", "entry_price": "100",
            "open_orders": [{"type": "STOP_MARKET", "reduce_only": True}],
        })
        result = recover_testnet_position(adapter, "BTCUSDT", 99, 101, "recover-06")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "recovery_order_status_unknown")
        self.assertTrue(result["requires_manual_reconciliation"])


if __name__ == "__main__":
    unittest.main()
