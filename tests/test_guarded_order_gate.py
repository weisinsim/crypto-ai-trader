import unittest

from validation.daily_loss_guard import DailyLossGuard
from validation.guarded_order_gate import evaluate_order_gate_with_daily_loss_guard


class GuardedOrderGateTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = {
            "exchange_healthy": True,
            "kill_switch_active": False,
            "live_execution_enabled": True,
            "candle_closed": True,
            "model_status": "PRODUCTION",
            "signal": "LONG",
            "data_age_sec": 1,
            "analysis_age_sec": 1,
            "spread_bps": 2,
            "risk_fraction": 0.01,
            "daily_loss_fraction": 0.0,
            "open_positions": 0,
            "requested_notional": 100,
        }
        self.limits = {
            "max_data_age_sec": 10,
            "max_analysis_age_sec": 10,
            "max_spread_bps": 10,
            "max_risk_fraction": 0.02,
            "max_daily_loss_fraction": 0.05,
            "max_open_positions": 3,
            "max_notional": 1000,
        }
        self.guard = DailyLossGuard(100)

    def test_unlocked_guard_preserves_base_gate_decision(self):
        result = evaluate_order_gate_with_daily_loss_guard(
            self.snapshot, self.limits, self.guard, "2026-10-14"
        )
        self.assertTrue(result["approved"])
        self.assertEqual(result["reasons"], [])

    def test_persistent_daily_lock_blocks_otherwise_approved_order(self):
        self.guard.record_realized_pnl("loss", -100, "2026-10-14")
        result = evaluate_order_gate_with_daily_loss_guard(
            self.snapshot, self.limits, self.guard, "2026-10-14"
        )
        self.assertFalse(result["approved"])
        self.assertIn("PERSISTENT_DAILY_LOSS_LOCKED", result["reasons"])

    def test_invalid_base_snapshot_still_fails_closed(self):
        self.snapshot["candle_closed"] = False
        result = evaluate_order_gate_with_daily_loss_guard(
            self.snapshot, self.limits, self.guard, "2026-10-14"
        )
        self.assertFalse(result["approved"])
        self.assertIn("CANDLE_NOT_CLOSED", result["reasons"])


if __name__ == "__main__":
    unittest.main()
