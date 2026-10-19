import sys
from pathlib import Path
import unittest

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


if __name__ == "__main__":
    unittest.main()
