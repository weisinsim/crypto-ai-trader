import unittest

from validation.testnet_reconciliation import reconcile_testnet_state


class TestnetReconciliationTests(unittest.TestCase):
    def snapshot(self, amount="0", orders=None, ok=True):
        return {"ok": ok, "position_amount": amount, "open_orders": orders or []}

    def protective(self, order_id="btc-stop-1", **overrides):
        return {
            "client_order_id": order_id,
            "type": "STOP_MARKET",
            "status": "NEW",
            "reduce_only": True,
            **overrides,
        }

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


if __name__ == "__main__":
    unittest.main()
