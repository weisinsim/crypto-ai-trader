import unittest

from validation.testnet_reconciliation import reconcile_testnet_state, validate_snapshot_timing


class TestnetReconciliationTests(unittest.TestCase):
    def snapshot(self, amount="0", orders=None, ok=True):
        return {"ok": ok, "position_amount": amount, "open_orders": orders or []}

    def protective(self, order_id="btc-stop-1", **overrides):
        return {
            "client_order_id": order_id,
            "type": "STOP_MARKET",
            "status": "NEW",
            "reduce_only": True,
            "side": "SELL",
            "orig_quantity": "0.5",
            "stop_price": "99",
            **overrides,
        }

    def test_snapshot_timing_accepts_fresh_bounded_observation(self):
        result = validate_snapshot_timing({
            "ok": True,
            "snapshot_started_at_ms": 1000,
            "snapshot_completed_at_ms": 1150,
            "snapshot_duration_ms": 150.0,
        }, now_ms=1200)
        self.assertTrue(result["ok"])
        self.assertEqual(result["observation_window_ms"], "150")

    def test_snapshot_timing_rejects_missing_stale_future_and_wide_windows(self):
        base = {
            "ok": True,
            "snapshot_started_at_ms": 1000,
            "snapshot_completed_at_ms": 1100,
            "snapshot_duration_ms": 100.0,
        }
        missing = validate_snapshot_timing({"ok": True}, now_ms=1200)
        stale = validate_snapshot_timing(base, now_ms=5000, max_age_ms=1000)
        future = validate_snapshot_timing(base, now_ms=900)
        wide = validate_snapshot_timing({
            **base,
            "snapshot_started_at_ms": 1000,
            "snapshot_completed_at_ms": 4000,
            "snapshot_duration_ms": 3000.0,
        }, now_ms=4100)
        self.assertEqual(missing["reason"], "SNAPSHOT_TIMING_INVALID")
        self.assertEqual(stale["reason"], "SNAPSHOT_STALE")
        self.assertEqual(future["reason"], "SNAPSHOT_TIMESTAMP_IN_FUTURE")
        self.assertEqual(wide["reason"], "SNAPSHOT_OBSERVATION_WINDOW_TOO_WIDE")

    def test_exact_flat_snapshot_passes(self):
        result = reconcile_testnet_state(
            {"BTCUSDT": "0"}, {"BTCUSDT": self.snapshot()}
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["reasons"], [])

    def test_quantity_tolerance_is_explicit_and_bounded(self):
        close = reconcile_testnet_state(
            {"BTCUSDT": "0.5"},
            {"BTCUSDT": self.snapshot("0.500000001")},
        )
        far = reconcile_testnet_state(
            {"BTCUSDT": "0.5"},
            {"BTCUSDT": self.snapshot("0.5001")},
        )
        self.assertTrue(close["ok"])
        self.assertIn("BTCUSDT:POSITION_QUANTITY_MISMATCH", far["reasons"])

    def test_missing_or_failed_snapshot_fails_closed(self):
        missing = reconcile_testnet_state({"BTCUSDT": "0.5"}, {})
        failed = reconcile_testnet_state(
            {"BTCUSDT": "0.5"}, {"BTCUSDT": self.snapshot("0.5", ok=False)}
        )
        self.assertFalse(missing["ok"])
        self.assertFalse(failed["ok"])
        self.assertIn("BTCUSDT:SNAPSHOT_MISSING_OR_FAILED", missing["reasons"])

    def test_required_protective_order_must_exist_once_and_be_reduce_only_open(self):
        valid = reconcile_testnet_state(
            {"BTCUSDT": "0.5"},
            {"BTCUSDT": self.snapshot("0.5", [self.protective()])},
            {"BTCUSDT": ["btc-stop-1"]},
        )
        missing = reconcile_testnet_state(
            {"BTCUSDT": "0.5"},
            {"BTCUSDT": self.snapshot("0.5")},
            {"BTCUSDT": ["btc-stop-1"]},
        )
        unsafe = reconcile_testnet_state(
            {"BTCUSDT": "0.5"},
            {"BTCUSDT": self.snapshot("0.5", [
                self.protective(reduce_only=False)
            ])},
            {"BTCUSDT": ["btc-stop-1"]},
        )
        self.assertTrue(valid["ok"])
        self.assertIn("BTCUSDT:PROTECTIVE_ORDER_MISSING_OR_AMBIGUOUS", missing["reasons"])
        self.assertIn("BTCUSDT:PROTECTIVE_ORDER_NOT_REDUCE_ONLY", unsafe["reasons"])

    def test_duplicate_order_ids_are_rejected(self):
        result = reconcile_testnet_state(
            {"BTCUSDT": "0.5"},
            {"BTCUSDT": self.snapshot("0.5", [
                self.protective(), self.protective()
            ])},
        )
        self.assertIn("BTCUSDT:DUPLICATE_OPEN_ORDER_ID", result["reasons"])

    def test_unexpected_nonzero_position_or_open_order_is_rejected(self):
        position = reconcile_testnet_state(
            {"BTCUSDT": "0"},
            {
                "BTCUSDT": self.snapshot(),
                "ETHUSDT": self.snapshot("0.1"),
            },
        )
        orders = reconcile_testnet_state(
            {"BTCUSDT": "0"},
            {
                "BTCUSDT": self.snapshot(),
                "ETHUSDT": self.snapshot("0", [self.protective("eth-stop-1")]),
            },
        )
        self.assertIn("ETHUSDT:UNEXPECTED_NONZERO_POSITION", position["reasons"])
        self.assertIn("ETHUSDT:UNEXPECTED_OPEN_ORDERS", orders["reasons"])

    def test_unlisted_open_order_is_rejected_even_if_required_protection_exists(self):
        result = reconcile_testnet_state(
            {"BTCUSDT": "0.5"},
            {"BTCUSDT": self.snapshot("0.5", [
                self.protective(),
                {
                    "client_order_id": "unexpected-entry",
                    "type": "LIMIT",
                    "status": "NEW",
                    "reduce_only": False,
                    "side": "BUY",
                    "orig_quantity": "0.5",
                    "stop_price": "100",
                },
            ])},
            {"BTCUSDT": ["btc-stop-1"]},
        )
        self.assertIn("BTCUSDT:UNEXPECTED_OPEN_ORDER", result["reasons"])

    def test_flat_position_with_any_open_order_is_not_reconciled(self):
        result = reconcile_testnet_state(
            {"BTCUSDT": "0"},
            {"BTCUSDT": self.snapshot("0", [self.protective()])},
        )
        self.assertIn("BTCUSDT:UNEXPECTED_OPEN_ORDER", result["reasons"])

    def test_invalid_numeric_and_boolean_values_fail_closed(self):
        for value in (True, "NaN", "Infinity", None):
            with self.subTest(value=value):
                result = reconcile_testnet_state(
                    {"BTCUSDT": value}, {"BTCUSDT": self.snapshot()}
                )
                self.assertFalse(result["ok"])
                self.assertIn("EXPECTED_QUANTITY_INVALID", result["reasons"])

    def test_duplicate_normalized_symbols_are_rejected(self):
        result = reconcile_testnet_state(
            {"btcusdt": "0", "BTCUSDT": "0"},
            {"BTCUSDT": self.snapshot()},
        )
        self.assertIn("DUPLICATE_NORMALIZED_SYMBOL", result["reasons"])

    def test_required_protective_order_side_quantity_and_type_are_checked(self):
        wrong_side = reconcile_testnet_state(
            {"BTCUSDT": "0.5"},
            {"BTCUSDT": self.snapshot("0.5", [self.protective(side="BUY")])},
            {"BTCUSDT": ["btc-stop-1"]},
        )
        wrong_quantity = reconcile_testnet_state(
            {"BTCUSDT": "0.5"},
            {"BTCUSDT": self.snapshot("0.5", [self.protective(orig_quantity="0.4")])},
            {"BTCUSDT": ["btc-stop-1"]},
        )
        wrong_type = reconcile_testnet_state(
            {"BTCUSDT": "0.5"},
            {"BTCUSDT": self.snapshot("0.5", [self.protective(type="LIMIT")])},
            {"BTCUSDT": ["btc-stop-1"]},
        )
        invalid_trigger = reconcile_testnet_state(
            {"BTCUSDT": "0.5"},
            {"BTCUSDT": self.snapshot("0.5", [self.protective(stop_price="NaN")])},
            {"BTCUSDT": ["btc-stop-1"]},
        )
        self.assertIn("BTCUSDT:PROTECTIVE_ORDER_SIDE_MISMATCH", wrong_side["reasons"])
        self.assertIn("BTCUSDT:PROTECTIVE_ORDER_QUANTITY_MISMATCH", wrong_quantity["reasons"])
        self.assertIn("BTCUSDT:PROTECTIVE_ORDER_TYPE_INVALID", wrong_type["reasons"])
        self.assertIn("BTCUSDT:PROTECTIVE_ORDER_TRIGGER_PRICE_INVALID", invalid_trigger["reasons"])

    def test_short_position_requires_buy_protective_side(self):
        result = reconcile_testnet_state(
            {"BTCUSDT": "-0.5"},
            {"BTCUSDT": self.snapshot("-0.5", [
                self.protective(side="BUY")
            ])},
            {"BTCUSDT": ["btc-stop-1"]},
        )
        self.assertTrue(result["ok"])


if __name__ == "__main__":
    unittest.main()
