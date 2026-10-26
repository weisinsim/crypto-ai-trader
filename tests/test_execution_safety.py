import unittest

from execution.safety import ExecutionConfig, OrderIntent, validate_order_intent


class ExecutionSafetyTests(unittest.TestCase):
    def intent(self, **overrides):
        values = dict(
            symbol="BTCUSDT",
            side="BUY",
            entry=100.0,
            stop_loss=99.8,
            take_profit=100.6,
            quantity=0.5,
            signal_timestamp=1000,
            now_timestamp=1100,
            leverage=1,
        )
        values.update(overrides)
        return OrderIntent(**values)

    def test_valid_testnet_intent_passes_only_pretrade_validation(self):
        decision = validate_order_intent(self.intent(), ExecutionConfig(mode="testnet"))
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason, "paper_or_testnet_validation_passed")
        self.assertAlmostEqual(decision.notional_usdt, 50.0)

    def test_rejects_boolean_and_non_numeric_order_values(self):
        for value in (True, "100", float("nan"), float("inf")):
            with self.subTest(value=value):
                decision = validate_order_intent(self.intent(entry=value), ExecutionConfig(mode="testnet"))
                self.assertFalse(decision.allowed)

    def test_rejects_boolean_or_non_integer_timestamps_and_leverage(self):
        cases = (
            {"signal_timestamp": True},
            {"now_timestamp": "1100"},
            {"leverage": True},
            {"leverage": 1.5},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                decision = validate_order_intent(self.intent(**overrides), ExecutionConfig(mode="testnet"))
                self.assertFalse(decision.allowed)

    def test_rejects_reduce_only_intent_on_entry_route(self):
        decision = validate_order_intent(self.intent(reduce_only=True), ExecutionConfig(mode="testnet"))
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "reduce_only_entry_not_supported")

    def test_rejects_wrong_object_types_and_unicode_symbol(self):
        self.assertFalse(validate_order_intent({}, ExecutionConfig()).allowed)
        self.assertFalse(validate_order_intent(self.intent(), {}).allowed)
        self.assertFalse(validate_order_intent(self.intent(side=[]), ExecutionConfig(mode="testnet")).allowed)
        self.assertFalse(validate_order_intent(self.intent(), ExecutionConfig(mode=[])).allowed)
        decision = validate_order_intent(self.intent(symbol="ＢＴＣUSDT"), ExecutionConfig(mode="testnet"))
        self.assertFalse(decision.allowed)

    def test_live_mode_and_live_flag_fail_closed(self):
        self.assertFalse(validate_order_intent(self.intent(), ExecutionConfig(mode="live")).allowed)
        self.assertFalse(
            validate_order_intent(
                self.intent(), ExecutionConfig(mode="testnet", live_trading_enabled=True)
            ).allowed
        )


if __name__ == "__main__":
    unittest.main()
