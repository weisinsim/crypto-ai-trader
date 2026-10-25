import unittest
from dataclasses import replace

from execution.safety import ExecutionConfig, OrderIntent, validate_order_intent


class ExecutionSafetyInputValidationTests(unittest.TestCase):
    def setUp(self):
        self.intent = OrderIntent(
            symbol="BTCUSDT",
            side="BUY",
            entry=100.0,
            stop_loss=99.9,
            take_profit=100.2,
            quantity=0.5,
            signal_timestamp=1000,
            now_timestamp=1100,
        )
        self.config = ExecutionConfig()

    def test_valid_paper_intent_is_accepted(self):
        result = validate_order_intent(self.intent, self.config)
        self.assertTrue(result.allowed)
        self.assertEqual(result.reason, "paper_or_testnet_validation_passed")

    def test_wrong_top_level_types_fail_closed(self):
        for intent, config, reason in (
            (None, self.config, "invalid_intent_type"),
            ({}, self.config, "invalid_intent_type"),
            (self.intent, None, "invalid_config_type"),
            (self.intent, {}, "invalid_config_type"),
        ):
            with self.subTest(reason=reason):
                result = validate_order_intent(intent, config)
                self.assertFalse(result.allowed)
                self.assertEqual(result.reason, reason)

    def test_malformed_numeric_values_fail_closed_without_raising(self):
        for field, value in (
            ("entry", None),
            ("entry", "100"),
            ("entry", True),
            ("entry", float("nan")),
            ("stop_loss", float("inf")),
            ("take_profit", float("-inf")),
            ("quantity", 10**10000),
        ):
            with self.subTest(field=field, value_type=type(value).__name__):
                result = validate_order_intent(replace(self.intent, **{field: value}), self.config)
                self.assertFalse(result.allowed)

    def test_malformed_symbols_and_flags_fail_closed(self):
        for symbol in (None, "btcusdt", "BTC/USDT", "USDT", "A" * 25 + "USDT"):
            with self.subTest(symbol=symbol):
                result = validate_order_intent(replace(self.intent, symbol=symbol), self.config)
                self.assertFalse(result.allowed)
                self.assertEqual(result.reason, "invalid_symbol")

        result = validate_order_intent(replace(self.intent, reduce_only=None), self.config)
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "invalid_reduce_only_flag")

    def test_timestamp_types_and_values_fail_closed(self):
        for field, value in (
            ("signal_timestamp", True),
            ("signal_timestamp", 1000.5),
            ("signal_timestamp", -1),
            ("now_timestamp", "1100"),
            ("now_timestamp", None),
        ):
            with self.subTest(field=field, value=value):
                result = validate_order_intent(replace(self.intent, **{field: value}), self.config)
                self.assertFalse(result.allowed)
                self.assertEqual(result.reason, "invalid_timestamp")

    def test_invalid_config_types_fail_closed(self):
        for field, value in (
            ("max_leverage", True),
            ("max_leverage", 2.0),
            ("max_signal_age_seconds", True),
            ("max_signal_age_seconds", 3600.0),
            ("max_notional_usdt", "100"),
            ("max_risk_fraction", float("nan")),
        ):
            with self.subTest(field=field, value=value):
                result = validate_order_intent(self.intent, replace(self.config, **{field: value}))
                self.assertFalse(result.allowed)

    def test_future_and_stale_signals_are_rejected(self):
        future = validate_order_intent(
            replace(self.intent, signal_timestamp=1200, now_timestamp=1100), self.config
        )
        stale = validate_order_intent(
            replace(self.intent, signal_timestamp=0, now_timestamp=5000), self.config
        )
        self.assertFalse(future.allowed)
        self.assertEqual(future.reason, "stale_or_future_signal")
        self.assertFalse(stale.allowed)
        self.assertEqual(stale.reason, "stale_or_future_signal")


if __name__ == "__main__":
    unittest.main()
