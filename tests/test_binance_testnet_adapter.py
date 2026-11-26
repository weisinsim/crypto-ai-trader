import hashlib
import hmac
import unittest
from urllib.parse import urlencode

try:
    from execution.binance_testnet import BinanceFuturesTestnet, BinanceTestnetConfigurationError, TESTNET_BASE_URL
    from execution.safety import ExecutionConfig, OrderIntent
except ModuleNotFoundError as exc:
    if exc.name == "execution" or (exc.name or "").startswith("execution."):
        raise unittest.SkipTest("Execution package is not present in this branch; testnet adapter tests require it")
    raise


class FakeResponse:
    def __init__(self, payload=None):
        self.payload = payload or {}

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self, exchange_payload=None):
        self.exchange_payload = exchange_payload or {"symbols": [{"symbol": "BTCUSDT", "status": "TRADING", "filters": [
            {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "100"},
            {"filterType": "MIN_NOTIONAL", "notional": "5"},
        ]}]}
        self.base_url = TESTNET_BASE_URL
        self.headers = {}
        self.calls = []
        self.get_calls = []

    def get(self, path, params):
        self.get_calls.append((path, params))
        if path == "/fapi/v1/openOrders":
            return FakeResponse([{"orderId": 99, "clientOrderId": "stop-0001", "side": "SELL", "type": "STOP_MARKET", "status": "NEW", "reduceOnly": True, "stopPrice": "99.8", "origQty": "0.5", "executedQty": "0"}])
        if path == "/fapi/v1/order":
            return FakeResponse({"symbol": "BTCUSDT", "clientOrderId": params.get("origClientOrderId"), "orderId": 12345, "status": "PARTIALLY_FILLED", "executedQty": "0.2", "avgPrice": "100.1"})
        if path == "/fapi/v2/account":
            return FakeResponse({"canTrade": True, "positions": [
                {"symbol": "BTCUSDT", "positionAmt": "0"},
                {"symbol": "ETHUSDT", "positionAmt": "0.25"},
            ]})
        return FakeResponse(self.exchange_payload)

    def post(self, path, params):
        self.calls.append((path, params))
        if path == "/fapi/v1/order":
            return FakeResponse({"orderId": 12345, "status": "NEW"})
        return FakeResponse()

    def delete(self, path, params):
        self.calls.append((path, params))
        return FakeResponse({"symbol": params.get("symbol"), "clientOrderId": params.get("origClientOrderId"), "orderId": 12345, "status": "CANCELED"})

    def close(self):
        pass


class BinanceTestnetAdapterTests(unittest.TestCase):
    def intent(self, **overrides):
        values = dict(
            symbol="BTCUSDT", side="BUY", entry=100, stop_loss=99.8,
            take_profit=100.6, quantity=0.5, signal_timestamp=1000,
            now_timestamp=1100, leverage=1,
        )
        values.update(overrides)
        return OrderIntent(**values)

    def test_requires_testnet_credentials(self):
        with self.assertRaises(BinanceTestnetConfigurationError):
            BinanceFuturesTestnet(api_key="", api_secret="")

    def test_posts_only_to_testnet_validation_endpoint(self):
        client = FakeClient()
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        result = adapter.validate_order(self.intent(), ExecutionConfig(mode="testnet"))
        self.assertTrue(result["accepted"])
        self.assertEqual(result["mode"], "testnet_validation_only")
        self.assertEqual(client.calls[0][0], "/fapi/v1/order/test")
        self.assertEqual(client.calls[0][1]["symbol"], "BTCUSDT")
        self.assertEqual(client.calls[0][1]["side"], "BUY")
        self.assertEqual(client.calls[0][1]["quantity"], "0.5")
        self.assertNotIn("https://fapi.binance.com", client.base_url)

    def test_account_preflight_uses_signed_testnet_read_only_endpoint(self):
        client = FakeClient()
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        result = adapter.account_preflight()
        self.assertEqual(result, {
            "connected": True, "mode": "testnet", "can_trade": True, "open_position_count": 1,
        })
        self.assertEqual(client.get_calls[0][0], "/fapi/v2/account")
        self.assertIn("signature", client.get_calls[0][1])
        self.assertEqual(client.calls, [])

    def test_account_preflight_rejects_unexpected_response(self):
        class BadClient(FakeClient):
            def get(self, path, params):
                return FakeResponse({"unexpected": True})
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=BadClient())
        with self.assertRaises(ValueError):
            adapter.account_preflight()

    def test_symbol_rules_reads_quantity_and_notional_filters(self):
        client = FakeClient()
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        rules = adapter.symbol_rules("BTCUSDT")
        self.assertEqual(rules["quantity_step"], "0.001")
        self.assertEqual(rules["min_quantity"], "0.001")
        self.assertEqual(rules["min_notional"], "5")
        self.assertEqual(client.get_calls[0][0], "/fapi/v1/exchangeInfo")
        self.assertEqual(client.calls, [])

    def test_symbol_rules_rejects_invalid_symbol(self):
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=FakeClient())
        with self.assertRaises(ValueError):
            adapter.symbol_rules("../BTCUSDT")

    def test_non_trading_symbol_is_rejected_before_order_validation(self):
        payload = {"symbols": [{"symbol": "BTCUSDT", "status": "BREAK", "filters": [
            {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "100"},
            {"filterType": "MIN_NOTIONAL", "notional": "5"},
        ]}]}
        client = FakeClient(exchange_payload=payload)
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        result = adapter.validate_order(self.intent(), ExecutionConfig(mode="testnet"))
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "symbol_not_trading")
        self.assertEqual(client.calls, [])

    def test_missing_quantity_filter_fails_closed(self):
        client = FakeClient(exchange_payload={"symbols": [{"symbol": "BTCUSDT", "status": "TRADING", "filters": []}]})
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        result = adapter.validate_order(self.intent(), ExecutionConfig(mode="testnet"))
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "exchange_preflight_failed")
        self.assertEqual(client.calls, [])

    def test_quantity_is_rounded_down_to_exchange_step(self):
        client = FakeClient()
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        result = adapter.validate_order(self.intent(quantity=0.5009), ExecutionConfig(mode="testnet"))
        self.assertTrue(result["accepted"])
        self.assertEqual(result["quantity"], "0.5")
        self.assertEqual(client.calls[0][1]["quantity"], "0.5")

    def test_quantity_below_exchange_minimum_is_rejected_without_order_request(self):
        client = FakeClient()
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        result = adapter.validate_order(self.intent(quantity=0.0009), ExecutionConfig(mode="testnet"))
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "quantity_below_exchange_minimum")
        self.assertEqual(client.calls, [])

    def test_exchange_minimum_notional_is_enforced(self):
        client = FakeClient()
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        result = adapter.validate_order(self.intent(quantity=0.01), ExecutionConfig(mode="testnet"))
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "notional_below_exchange_minimum")
        self.assertEqual(client.calls, [])

    def test_testnet_market_order_uses_client_order_id_and_testnet_endpoint(self):
        client = FakeClient()
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        result = adapter.submit_testnet_market_order(
            self.intent(quantity=0.5009), "paper-test-0001", ExecutionConfig(mode="testnet")
        )
        self.assertTrue(result["accepted"])
        self.assertEqual(result["mode"], "testnet_order_submitted")
        self.assertEqual(result["exchange_order_id"], "12345")
        self.assertEqual(client.calls[0][0], "/fapi/v1/order")
        self.assertEqual(client.calls[0][1]["newClientOrderId"], "paper-test-0001")
        self.assertEqual(client.calls[0][1]["quantity"], "0.5")
        self.assertNotIn("https://fapi.binance.com", client.base_url)

    def test_testnet_order_rejects_paper_or_live_mode_without_submission(self):
        client = FakeClient()
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        result = adapter.submit_testnet_market_order(
            self.intent(), "paper-test-0002", ExecutionConfig(mode="paper")
        )
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "testnet_mode_required")
        self.assertEqual(client.calls, [])

    def test_testnet_order_requires_safe_client_order_id(self):
        client = FakeClient()
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        result = adapter.submit_testnet_market_order(
            self.intent(), "bad id with spaces", ExecutionConfig(mode="testnet")
        )
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "invalid_client_order_id")
        self.assertEqual(client.calls, [])

    def test_query_order_reconciles_by_client_order_id_without_submission(self):
        client = FakeClient()
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        result = adapter.query_testnet_order("BTCUSDT", "paper-test-0001")
        self.assertTrue(result["found"])
        self.assertEqual(result["status"], "PARTIALLY_FILLED")
        self.assertEqual(result["executed_quantity"], "0.2")
        self.assertEqual(client.get_calls[0][0], "/fapi/v1/order")
        self.assertEqual(client.get_calls[0][1]["origClientOrderId"], "paper-test-0001")
        self.assertIn("signature", client.get_calls[0][1])
        self.assertEqual(client.calls, [])

    def test_query_order_rejects_invalid_client_id_without_network_call(self):
        client = FakeClient()
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        result = adapter.query_testnet_order("BTCUSDT", "invalid id")
        self.assertFalse(result["found"])
        self.assertEqual(result["reason"], "invalid_client_order_id")
        self.assertEqual(client.get_calls, [])

    def test_position_snapshot_reads_position_and_open_orders_without_mutation(self):
        client = FakeClient()
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        result = adapter.testnet_position_snapshot("BTCUSDT")
        self.assertTrue(result["ok"])
        self.assertEqual(result["mode"], "testnet_read_only_snapshot")
        self.assertEqual(result["position_amount"], "0")
        self.assertEqual(result["open_orders"][0]["type"], "STOP_MARKET")
        self.assertTrue(result["open_orders"][0]["reduce_only"])
        self.assertEqual([call[0] for call in client.get_calls[:2]], ["/fapi/v2/account", "/fapi/v1/openOrders"])
        self.assertEqual(client.calls, [])

    def test_position_snapshot_rejects_invalid_symbol_without_network_call(self):
        client = FakeClient()
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        result = adapter.testnet_position_snapshot("../BTCUSDT")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "invalid_symbol")
        self.assertEqual(client.get_calls, [])

    def test_protective_stop_submits_reduce_only_exit_for_existing_testnet_position(self):
        class PositionClient(FakeClient):
            def get(self, path, params):
                if path == "/fapi/v2/account":
                    self.get_calls.append((path, params))
                    return FakeResponse({"canTrade": True, "positions": [
                        {"symbol": "BTCUSDT", "positionAmt": "0.5", "entryPrice": "100"},
                    ]})
                return super().get(path, params)
        client = PositionClient()
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        result = adapter.submit_testnet_protective_exit(
            "BTCUSDT", "stop_loss", "0.5", "99.8", "stop-btc-0001"
        )
        self.assertTrue(result["accepted"])
        self.assertEqual(result["type"], "STOP_MARKET")
        self.assertEqual(result["side"], "SELL")
        self.assertTrue(result["reduce_only"])
        self.assertEqual(result["quantity"], "0.5")
        order_call = next(call for call in client.calls if call[0] == "/fapi/v1/order")
        self.assertEqual(order_call[1]["reduceOnly"], "true")
        self.assertEqual(order_call[1]["workingType"], "MARK_PRICE")
        self.assertEqual(order_call[1]["newClientOrderId"], "stop-btc-0001")

    def test_protective_exit_rejects_trigger_geometry_and_oversized_quantity(self):
        class PositionClient(FakeClient):
            def get(self, path, params):
                if path == "/fapi/v2/account":
                    self.get_calls.append((path, params))
                    return FakeResponse({"canTrade": True, "positions": [
                        {"symbol": "BTCUSDT", "positionAmt": "0.5", "entryPrice": "100"},
                    ]})
                return super().get(path, params)
        client = PositionClient()
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        wrong_trigger = adapter.submit_testnet_protective_exit(
            "BTCUSDT", "stop_loss", "0.5", "100.2", "stop-btc-0002"
        )
        too_large = adapter.submit_testnet_protective_exit(
            "BTCUSDT", "take_profit", "0.6", "101", "take-btc-0001"
        )
        self.assertEqual(wrong_trigger["reason"], "invalid_protective_trigger_geometry")
        self.assertEqual(too_large["reason"], "exit_quantity_exceeds_position")
        self.assertEqual(client.calls, [])

    def test_signed_request_uses_hmac_sha256(self):
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=FakeClient())
        params = adapter._signed_params({"symbol": "BTCUSDT", "side": "BUY"})
        signature = params.pop("signature")
        unsigned = urlencode(params)
        expected = hmac.new(b"test-secret", unsigned.encode(), hashlib.sha256).hexdigest()
        self.assertEqual(signature, expected)

    def test_invalid_intent_does_not_send_request(self):
        client = FakeClient()
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        result = adapter.validate_order(
            self.intent(stop_loss=101),
            ExecutionConfig(mode="testnet"),
        )
        self.assertFalse(result["accepted"])
        self.assertEqual(client.calls, [])

    def test_paper_mode_does_not_send_request(self):
        client = FakeClient()
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        result = adapter.validate_order(self.intent(), ExecutionConfig(mode="paper"))
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "testnet_mode_required")
        self.assertEqual(client.calls, [])


    def test_cancel_order_uses_signed_specific_testnet_client_id(self):
        client = FakeClient()
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        result = adapter.cancel_testnet_order("BTCUSDT", "btc-stop-0001")
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["mode"], "testnet_order_cancelled")
        self.assertEqual(client.calls[0][0], "/fapi/v1/order")
        self.assertEqual(client.calls[0][1]["origClientOrderId"], "btc-stop-0001")
        self.assertIn("signature", client.calls[0][1])

    def test_cancel_order_rejects_invalid_id_without_request(self):
        client = FakeClient()
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        result = adapter.cancel_testnet_order("BTCUSDT", "bad client id")
        self.assertFalse(result["cancelled"])
        self.assertEqual(result["reason"], "invalid_client_order_id")
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
