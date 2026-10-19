import hashlib
import hmac
import unittest
from urllib.parse import urlencode

from execution.binance_testnet import BinanceFuturesTestnet, BinanceTestnetConfigurationError, TESTNET_BASE_URL
from execution.safety import ExecutionConfig, OrderIntent


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
        return FakeResponse(self.exchange_payload)

    def post(self, path, params):
        self.calls.append((path, params))
        return FakeResponse()

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


if __name__ == "__main__":
    unittest.main()
