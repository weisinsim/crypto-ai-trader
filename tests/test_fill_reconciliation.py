import unittest

from execution.fill_reconciliation import normalize_order_payload, normalize_trade_payload


class FillReconciliationTests(unittest.TestCase):
    def order(self, **overrides):
        payload = {
            "orderId": 123,
            "clientOrderId": "client-1",
            "symbol": "BTCUSDT",
            "status": "PARTIALLY_FILLED",
            "executedQty": "0.25",
            "origQty": "1.0",
        }
        payload.update(overrides)
        return payload

    def trade(self, **overrides):
        payload = {
            "id": 77,
            "orderId": 123,
            "clientOrderId": "client-1",
            "symbol": "BTCUSDT",
            "side": "BUY",
            "qty": "0.25",
            "price": "65000.1",
            "commission": "0.01",
            "realizedPnl": "0",
            "time": 1780000000000,
        }
        payload.update(overrides)
        return payload

    def test_normalize_partial_order(self):
        result = normalize_order_payload(self.order())
        self.assertEqual(result["status"], "PARTIALLY_FILLED")
        self.assertEqual(result["cumulative_filled_qty"], 0.25)
        self.assertFalse(result["terminal"])

    def test_filled_requires_matching_quantity(self):
        result = normalize_order_payload(
            self.order(status="FILLED", executedQty="1.0"))
        self.assertTrue(result["terminal"])
        with self.assertRaisesRegex(ValueError, "filled_quantity_mismatch"):
            normalize_order_payload(self.order(status="FILLED", executedQty="0.5"))

    def test_huge_decimal_quantity_cannot_overflow_float_output(self):
        with self.assertRaisesRegex(ValueError, "invalid_executed_qty"):
            normalize_order_payload(self.order(
                executedQty="1e9999", origQty="1e10000",
            ))

    def test_unknown_status_fails_closed(self):
        for status in ("PENDING_CANCEL", [], {}):
            with self.subTest(status=status):
                with self.assertRaisesRegex(ValueError, "unknown_order_status"):
                    normalize_order_payload(self.order(status=status))

    def test_executed_quantity_cannot_exceed_original(self):
        with self.assertRaisesRegex(ValueError, "executed_qty_exceeds_original"):
            normalize_order_payload(self.order(executedQty="1.1"))

    def test_trade_event_id_is_stable_and_scoped(self):
        first = normalize_trade_payload(self.trade())
        second = normalize_trade_payload(self.trade())
        self.assertEqual(first["event_id"], second["event_id"])
        self.assertEqual(first["event_id"], "binance-usdm:BTCUSDT:123:77")
        self.assertEqual(first["quantity"], "0.25")

    def test_trade_ids_do_not_collide_across_symbols_or_orders(self):
        first = normalize_trade_payload(self.trade())
        second = normalize_trade_payload(self.trade(orderId=124))
        third = normalize_trade_payload(self.trade(symbol="ETHUSDT"))
        self.assertNotEqual(first["event_id"], second["event_id"])
        self.assertNotEqual(first["event_id"], third["event_id"])

    def test_invalid_trade_data_rejected(self):
        for payload, error in [
            (self.trade(qty="0"), "invalid_trade_qty"),
            (self.trade(price="NaN"), "invalid_trade_price"),
            (self.trade(side="LONG"), "invalid_trade_side"),
            (self.trade(side=[]), "invalid_trade_side"),
            (self.trade(side={}), "invalid_trade_side"),
            (self.trade(id=None), "trade_id_missing"),
            (self.trade(realizedPnl="Infinity"), "invalid_realized_pnl"),
            (self.trade(time=True), "invalid_trade_timestamp"),
            (self.trade(time=-1), "invalid_trade_timestamp"),
            (self.trade(time="1780000000000"), "invalid_trade_timestamp"),
            (self.trade(time=None), "invalid_trade_timestamp"),
            (self.trade(time=10**100), "invalid_trade_timestamp"),
        ]:
            with self.subTest(error=error):
                with self.assertRaisesRegex(ValueError, error):
                    normalize_trade_payload(payload)

    def test_negative_realized_pnl_is_allowed(self):
        result = normalize_trade_payload(self.trade(realizedPnl="-1.25"))
        self.assertEqual(result["realized_pnl"], "-1.25")


if __name__ == "__main__":
    unittest.main()
