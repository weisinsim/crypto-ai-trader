import unittest

from execution.safety import ExecutionConfig, OrderIntent, validate_order_intent


class ExecutionSafetyTests(unittest.TestCase):
    def intent(self, **overrides):
        values = dict(
            symbol="BTCUSDT", side="BUY", entry=100.0, stop_loss=99.8,
            take_profit=100.6, quantity=0.5, signal_timestamp=1000,
            now_timestamp=1100, leverage=1,
        )
        values.update(overrides)
        return OrderIntent(**values)

    def test_valid_paper_intent_is_accepted_for_simulation_only(self):
        result = validate_order_intent(self.intent(), ExecutionConfig())
        self.assertTrue(result.allowed)
        self.assertEqual(result.reason, "paper_or_testnet_validation_passed")
        self.assertAlmostEqual(result.notional_usdt, 50.0)
        self.assertAlmostEqual(result.risk_usdt, 0.1)

    def test_live_trading_is_always_blocked(self):
        result = validate_order_intent(
            self.intent(),
            ExecutionConfig(mode="paper", live_trading_enabled=True),
        )
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "live_trading_not_implemented")

    def test_stale_signal_is_rejected(self):
        result = validate_order_intent(
            self.intent(now_timestamp=5000),
            ExecutionConfig(max_signal_age_seconds=3600),
        )
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "stale_or_future_signal")

    def test_future_signal_is_rejected(self):
        result = validate_order_intent(self.intent(signal_timestamp=1200, now_timestamp=1100), ExecutionConfig())
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "stale_or_future_signal")

    def test_bad_long_stop_geometry_is_rejected(self):
        result = validate_order_intent(self.intent(stop_loss=101.0), ExecutionConfig())
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "invalid_long_risk_geometry")

    def test_short_geometry_is_checked(self):
        result = validate_order_intent(
            self.intent(side="SELL", stop_loss=99.8, take_profit=99.5),
            ExecutionConfig(),
        )
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "invalid_short_risk_geometry")

    def test_notional_and_leverage_limits_are_enforced(self):
        result = validate_order_intent(self.intent(quantity=2), ExecutionConfig(max_notional_usdt=100))
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "notional_limit_exceeded")
        result = validate_order_intent(self.intent(leverage=3), ExecutionConfig(max_leverage=2))
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "leverage_limit_exceeded")


    def test_non_finite_values_are_rejected(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            result = validate_order_intent(self.intent(entry=value), ExecutionConfig())
            self.assertFalse(result.allowed)
            self.assertEqual(result.reason, "non_positive_price_quantity_or_limit")

    def test_invalid_risk_configuration_is_rejected(self):
        result = validate_order_intent(self.intent(), ExecutionConfig(max_risk_fraction=1.5))
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "invalid_risk_configuration")

    def test_unsupported_mode_fails_closed(self):
        result = validate_order_intent(self.intent(), ExecutionConfig(mode="live"))
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "unsupported_mode")


if __name__ == "__main__":
    unittest.main()
