import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from execution.runtime_guard import (
    RuntimeRiskConfig,
    RuntimeSnapshot,
    validate_runtime_state,
)


class RuntimeGuardTests(unittest.TestCase):
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

    def test_allows_fresh_state_within_limits(self):
        result = validate_runtime_state(self.snapshot())
        self.assertTrue(result.allowed)

    def test_missing_or_stale_state_fails_closed(self):
        self.assertEqual(validate_runtime_state(None).reason, "runtime_snapshot_missing")
        self.assertEqual(
            validate_runtime_state(self.snapshot(snapshot_age_seconds=31)).reason,
            "runtime_snapshot_stale",
        )

    def test_emergency_stop_blocks(self):
        result = validate_runtime_state(self.snapshot(emergency_stop=True))
        self.assertEqual(result.reason, "emergency_stop_active")

    def test_daily_loss_limit_blocks_at_boundary(self):
        result = validate_runtime_state(self.snapshot(daily_realized_pnl_usdt=-10.0))
        self.assertEqual(result.reason, "daily_loss_limit_reached")

    def test_position_and_notional_limits_block(self):
        self.assertEqual(
            validate_runtime_state(self.snapshot(open_positions=1)).reason,
            "open_position_limit_reached",
        )
        self.assertEqual(
            validate_runtime_state(self.snapshot(symbol_notional_usdt=100.0)).reason,
            "symbol_notional_limit_reached",
        )
        self.assertEqual(
            validate_runtime_state(self.snapshot(total_notional_usdt=100.0)).reason,
            "total_notional_limit_reached",
        )

    def test_execution_error_circuit_breaker_blocks(self):
        self.assertEqual(
            validate_runtime_state(self.snapshot(consecutive_execution_errors=3)).reason,
            "execution_error_circuit_breaker_open",
        )

    def test_nonfinite_or_negative_state_fails_closed(self):
        self.assertEqual(
            validate_runtime_state(self.snapshot(total_notional_usdt=float("nan"))).reason,
            "runtime_snapshot_non_finite",
        )
        self.assertEqual(
            validate_runtime_state(self.snapshot(open_positions=-1)).reason,
            "runtime_snapshot_invalid",
        )

    def test_malformed_snapshot_types_fail_closed_without_raising(self):
        self.assertEqual(
            validate_runtime_state(self.snapshot(total_notional_usdt="not-a-number")).reason,
            "runtime_snapshot_invalid",
        )
        self.assertEqual(
            validate_runtime_state(self.snapshot(open_positions=True)).reason,
            "runtime_snapshot_invalid",
        )
        self.assertEqual(
            validate_runtime_state(self.snapshot(emergency_stop=1)).reason,
            "runtime_snapshot_invalid",
        )

    def test_malformed_risk_config_fails_closed_without_raising(self):
        config = RuntimeRiskConfig(max_daily_loss_usdt="bad")
        self.assertEqual(
            validate_runtime_state(self.snapshot(), config).reason,
            "invalid_runtime_risk_configuration",
        )
        config = RuntimeRiskConfig(max_open_positions=True)
        self.assertEqual(
            validate_runtime_state(self.snapshot(), config).reason,
            "invalid_runtime_risk_configuration",
        )

    def test_invalid_config_fails_closed(self):
        config = RuntimeRiskConfig(max_daily_loss_usdt=0)
        self.assertEqual(
            validate_runtime_state(self.snapshot(), config).reason,
            "invalid_runtime_risk_configuration",
        )


if __name__ == "__main__":
    unittest.main()
