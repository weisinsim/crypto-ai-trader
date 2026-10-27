import hashlib
import hmac
import time
import unittest
from urllib.parse import urlencode

import httpx

from execution.binance_testnet import BinanceFuturesTestnet, BinanceTestnetConfigurationError, TESTNET_BASE_URL
from execution.safety import ExecutionConfig, OrderIntent


class FakeResponse:
    def __init__(self, payload=None):
        self.payload = payload if payload is not None else {}

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self, exchange_payload=None):
        self.exchange_payload = exchange_payload if exchange_payload is not None else {"symbols": [{"symbol": "BTCUSDT", "status": "TRADING", "filters": [
            {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "100"},
            {"filterType": "MIN_NOTIONAL", "notional": "5"},
            {"filterType": "PRICE_FILTER", "tickSize": "0.1", "minPrice": "0.1", "maxPrice": "1000000"},
        ]}]}
        self.base_url = TESTNET_BASE_URL
        self.headers = {}
        self.calls = []
        self.get_calls = []

    def get(self, path, params):
        self.get_calls.append((path, params))
        if path == "/fapi/v1/openOrders":
            return FakeResponse([])
        if path == "/fapi/v1/premiumIndex":
            return FakeResponse({"symbol": params.get("symbol"), "markPrice": "100"})
        if path == "/fapi/v1/order":
            return FakeResponse({"symbol": "BTCUSDT", "clientOrderId": params.get("origClientOrderId"), "orderId": 12345, "status": "PARTIALLY_FILLED", "origQty": "0.5", "executedQty": "0.2", "avgPrice": "100.1"})
        if path == "/fapi/v2/account":
            return FakeResponse({"canTrade": True, "positions": [
                {"symbol": "BTCUSDT", "positionAmt": "0", "entryPrice": "0", "notional": "0"},
                {"symbol": "ETHUSDT", "positionAmt": "0.25", "entryPrice": "100", "notional": "25"},
            ]})
        return FakeResponse(self.exchange_payload)

    def post(self, path, params):
        self.calls.append((path, params))
        if path == "/fapi/v1/order":
            payload = {
                "symbol": params.get("symbol"),
                "clientOrderId": params.get("newClientOrderId"),
                "orderId": 12345,
                "status": "NEW",
            }
            if params.get("type") in {"STOP_MARKET", "TAKE_PROFIT_MARKET"}:
                payload.update({
                    "side": params.get("side"),
                    "type": params.get("type"),
                    "reduceOnly": True,
                    "workingType": params.get("workingType"),
                    "origQty": params.get("quantity"),
                    "stopPrice": params.get("stopPrice"),
                })
            return FakeResponse(payload)
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

    def test_rejects_injected_client_with_non_testnet_base_url(self):
        client = FakeClient()
        client.base_url = "https://fapi.binance.com"
        with self.assertRaises(BinanceTestnetConfigurationError):
            BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        self.assertEqual(client.calls, [])

    def test_rechecks_testnet_host_before_each_request(self):
        client = FakeClient()
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        client.base_url = "https://fapi.binance.com"
        with self.assertRaises(BinanceTestnetConfigurationError):
            adapter.account_preflight()
        self.assertEqual(client.get_calls, [])
        self.assertEqual(client.calls, [])

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

    def test_account_preflight_rejects_non_boolean_trade_flag_and_nonfinite_position(self):
        class BadClient(FakeClient):
            def __init__(self, payload):
                super().__init__()
                self.payload = payload
            def get(self, path, params):
                return FakeResponse(self.payload)
        payloads = (
            {"canTrade": "false", "positions": []},
            {"canTrade": True, "positions": [{"symbol": "BTCUSDT", "positionAmt": "NaN", "entryPrice": "100"}]},
            {"canTrade": True, "positions": "malformed"},
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                adapter = BinanceFuturesTestnet(
                    api_key="test-key", api_secret="test-secret", client=BadClient(payload)
                )
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
        for symbol in ("../BTCUSDT", "ＢＴＣUSDT", None):
            with self.subTest(symbol=symbol):
                with self.assertRaises(ValueError):
                    adapter.symbol_rules(symbol)

    def test_symbol_rules_rejects_malformed_exchange_info_and_nonfinite_filters(self):
        payloads = (
            [],
            {"symbols": [None]},
            {"symbols": [{"symbol": "BTCUSDT", "filters": "malformed"}]},
            {"symbols": [{"symbol": "BTCUSDT", "filters": [
                {"filterType": "MARKET_LOT_SIZE", "stepSize": "NaN", "minQty": "0.001", "maxQty": "100"}
            ]}]},
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                client = FakeClient(exchange_payload=payload)
                adapter = BinanceFuturesTestnet(
                    api_key="test-key", api_secret="test-secret", client=client
                )
                with self.assertRaises(ValueError):
                    adapter.symbol_rules("BTCUSDT")

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

    def test_testnet_order_rejects_falsey_invalid_config_without_submission(self):
        client = FakeClient()
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        result = adapter.submit_testnet_market_order(self.intent(), "paper-test-0003", {})
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "invalid_execution_config")
        validation = adapter.validate_order(self.intent(), {})
        self.assertFalse(validation["accepted"])
        self.assertEqual(validation["reason"], "invalid_execution_config")
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

    def test_market_order_fails_closed_when_account_risk_snapshot_is_unavailable(self):
        class UnavailableAccountClient(FakeClient):
            def get(self, path, params):
                if path == "/fapi/v2/account":
                    self.get_calls.append((path, params))
                    raise httpx.ReadTimeout("simulated account risk timeout")
                return super().get(path, params)

        client = UnavailableAccountClient()
        adapter = BinanceFuturesTestnet(
            api_key="test-key", api_secret="test-secret", client=client
        )
        result = adapter.submit_testnet_market_order(
            self.intent(), "risk-timeout-0001", ExecutionConfig(mode="testnet")
        )
        self.assertFalse(result["accepted"])
        self.assertEqual(
            result["reason"], "account_risk_preflight_failed_requires_reconciliation"
        )
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(client.calls, [])

    def test_market_order_fails_closed_when_projected_account_exposure_exceeds_cap(self):
        client = FakeClient()
        adapter = BinanceFuturesTestnet(
            api_key="test-key", api_secret="test-secret", client=client
        )
        result = adapter.submit_testnet_market_order(
            self.intent(),
            "risk-cap-0001",
            ExecutionConfig(mode="testnet", max_account_notional_usdt=60.0),
        )
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "account_notional_limit_exceeded")
        self.assertEqual(result["current_account_notional_usdt"], 25.0)
        self.assertEqual(result["proposed_order_notional_usdt"], 50.0)
        self.assertEqual(result["max_account_notional_usdt"], 60.0)
        self.assertEqual(client.calls, [])

    def test_market_order_fails_closed_when_existing_position_notional_is_missing(self):
        class MissingExistingNotionalClient(FakeClient):
            def get(self, path, params):
                if path == "/fapi/v2/account":
                    self.get_calls.append((path, params))
                    return FakeResponse({
                        "canTrade": True,
                        "positions": [{
                            "symbol": "ETHUSDT",
                            "positionAmt": "0.25",
                            "entryPrice": "100",
                        }],
                    })
                return super().get(path, params)

        client = MissingExistingNotionalClient()
        adapter = BinanceFuturesTestnet(
            api_key="test-key", api_secret="test-secret", client=client
        )
        result = adapter.submit_testnet_market_order(
            self.intent(), "risk-missing-0001", ExecutionConfig(mode="testnet")
        )
        self.assertFalse(result["accepted"])
        self.assertEqual(
            result["reason"], "account_risk_preflight_failed_requires_reconciliation"
        )
        self.assertTrue(result["requires_reconciliation"])
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

    def test_query_order_http_404_is_not_treated_as_confirmed_absence(self):
        class MissingOrderClient(FakeClient):
            def get(self, path, params):
                self.get_calls.append((path, params))
                if path == "/fapi/v1/order":
                    request = httpx.Request(
                        "GET", "https://testnet.binancefuture.com/fapi/v1/order"
                    )
                    response = httpx.Response(404, json={
                        "code": -2013, "msg": "Order does not exist."
                    }, request=request)
                    raise httpx.HTTPStatusError(
                        "order lookup returned 404", request=request, response=response
                    )
                return super().get(path, params)

        client = MissingOrderClient()
        adapter = BinanceFuturesTestnet(
            api_key="test-key", api_secret="test-secret", client=client
        )
        result = adapter.query_testnet_order("BTCUSDT", "paper-test-404")
        self.assertFalse(result["found"])
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(
            result["reason"],
            "testnet_order_query_http_outcome_requires_reconciliation",
        )
        self.assertEqual(result["http_status"], 404)
        self.assertEqual(len([call for call in client.get_calls if call[0] == "/fapi/v1/order"]), 1)
        self.assertEqual(client.calls, [])

    def test_query_order_transport_failure_requires_reconciliation(self):
        class BrokenReadClient(FakeClient):
            def get(self, path, params):
                if path == "/fapi/v1/order":
                    self.get_calls.append((path, params))
                    request = httpx.Request("GET", "https://testnet.binancefuture.com/fapi/v1/order")
                    raise httpx.ReadTimeout("simulated read timeout", request=request)
                return super().get(path, params)

        client = BrokenReadClient()
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        result = adapter.query_testnet_order("BTCUSDT", "paper-test-timeout")
        self.assertFalse(result["found"])
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(result["reason"], "testnet_order_query_outcome_unknown")
        self.assertEqual(len(client.get_calls), 1)
        self.assertEqual(client.calls, [])

    def test_query_order_unreadable_json_requires_reconciliation(self):
        class UnreadableResponse(FakeResponse):
            def json(self):
                raise ValueError("invalid JSON")

        class BrokenJsonClient(FakeClient):
            def get(self, path, params):
                if path == "/fapi/v1/order":
                    self.get_calls.append((path, params))
                    return UnreadableResponse({})
                return super().get(path, params)

        client = BrokenJsonClient()
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        result = adapter.query_testnet_order("BTCUSDT", "paper-test-invalid-json")
        self.assertFalse(result["found"])
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(
            result["reason"],
            "testnet_order_query_response_unreadable_requires_reconciliation",
        )
        self.assertEqual(len(client.get_calls), 1)
        self.assertEqual(client.calls, [])

    def test_query_order_rejects_mismatched_exchange_identity(self):
        class MismatchedClient(FakeClient):
            def get(self, path, params):
                if path == "/fapi/v1/order":
                    self.get_calls.append((path, params))
                    return FakeResponse({
                        "symbol": "ETHUSDT",
                        "clientOrderId": "different-client-id",
                        "orderId": 12345,
                        "status": "FILLED",
                        "executedQty": "0.5",
                    })
                return super().get(path, params)

        client = MismatchedClient()
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        result = adapter.query_testnet_order("BTCUSDT", "paper-test-0001")
        self.assertFalse(result["found"])
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(result["reason"], "unexpected_or_mismatched_testnet_order_response")
        self.assertEqual(client.calls, [])

    def test_query_order_rejects_invalid_client_id_without_network_call(self):
        client = FakeClient()
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        for client_id in ("invalid id", "客户端-0001"):
            with self.subTest(client_id=client_id):
                result = adapter.query_testnet_order("BTCUSDT", client_id)
                self.assertFalse(result["found"])
                self.assertEqual(result["reason"], "invalid_client_order_id")
        self.assertEqual(client.get_calls, [])

    def test_position_snapshot_reads_position_and_open_orders_without_mutation(self):
        class SnapshotOrderClient(FakeClient):
            def get(self, path, params):
                if path == "/fapi/v1/openOrders":
                    self.get_calls.append((path, params))
                    return FakeResponse([{
                        "orderId": 99, "symbol": "BTCUSDT", "clientOrderId": "stop-0001",
                        "side": "SELL", "type": "STOP_MARKET", "status": "NEW",
                        "reduceOnly": True, "stopPrice": "99.8", "origQty": "0.5",
                        "executedQty": "0",
                    }])
                return super().get(path, params)
        client = SnapshotOrderClient()
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        result = adapter.testnet_position_snapshot("BTCUSDT")
        self.assertTrue(result["ok"])
        self.assertEqual(result["mode"], "testnet_read_only_snapshot")
        self.assertGreater(result["snapshot_started_at_ms"], 0)
        self.assertGreaterEqual(result["snapshot_completed_at_ms"], result["snapshot_started_at_ms"])
        self.assertEqual(
            result["snapshot_duration_ms"],
            float(result["snapshot_completed_at_ms"] - result["snapshot_started_at_ms"]),
        )
        self.assertEqual(result["position_amount"], "0")
        self.assertEqual(result["open_orders"][0]["type"], "STOP_MARKET")
        self.assertTrue(result["open_orders"][0]["reduce_only"])
        self.assertEqual([call[0] for call in client.get_calls[:2]], ["/fapi/v2/account", "/fapi/v1/openOrders"])
        self.assertEqual(client.calls, [])

    def test_position_snapshot_rejects_open_order_for_wrong_symbol(self):
        class WrongSymbolOrderClient(FakeClient):
            def get(self, path, params):
                if path == "/fapi/v1/openOrders":
                    self.get_calls.append((path, params))
                    return FakeResponse([{
                        "orderId": 99, "symbol": "ETHUSDT", "clientOrderId": "stop-0001",
                        "side": "SELL", "type": "STOP_MARKET", "status": "NEW",
                        "reduceOnly": True, "stopPrice": "99.8", "origQty": "0.5",
                        "executedQty": "0",
                    }])
                return super().get(path, params)

        client = WrongSymbolOrderClient()
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        result = adapter.testnet_position_snapshot("BTCUSDT")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "testnet_open_order_symbol_mismatch")
        self.assertEqual(client.calls, [])

    def test_position_snapshot_rejects_nonfinite_open_order_quantity(self):
        class InvalidQuantityOrderClient(FakeClient):
            def get(self, path, params):
                if path == "/fapi/v1/openOrders":
                    self.get_calls.append((path, params))
                    return FakeResponse([{
                        "orderId": 99, "symbol": "BTCUSDT", "clientOrderId": "stop-0001",
                        "side": "SELL", "type": "STOP_MARKET", "status": "NEW",
                        "reduceOnly": True, "stopPrice": "99.8", "origQty": "NaN",
                        "executedQty": "0",
                    }])
                return super().get(path, params)

        client = InvalidQuantityOrderClient()
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        result = adapter.testnet_position_snapshot("BTCUSDT")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "invalid_testnet_open_order_numeric_fields")
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
        off_tick = adapter.submit_testnet_protective_exit(
            "BTCUSDT", "stop_loss", "0.5", "99.85", "stop-btc-offtick"
        )
        out_of_bounds = adapter.submit_testnet_protective_exit(
            "BTCUSDT", "stop_loss", "0.5", "0.05", "stop-btc-outbounds"
        )
        too_large = adapter.submit_testnet_protective_exit(
            "BTCUSDT", "take_profit", "0.6", "101", "take-btc-0001"
        )
        self.assertEqual(wrong_trigger["reason"], "invalid_protective_trigger_geometry")
        self.assertEqual(off_tick["reason"], "trigger_price_not_aligned_to_exchange_tick")
        self.assertEqual(out_of_bounds["reason"], "trigger_price_outside_exchange_price_bounds")
        self.assertEqual(too_large["reason"], "exit_quantity_exceeds_position")
        self.assertEqual(client.calls, [])

    def test_protective_exit_rejects_snapshot_that_aged_during_preflight(self):
        class AgedSnapshotAdapter(BinanceFuturesTestnet):
            def testnet_position_snapshot(self, symbol):
                completed = int(time.time() * 1000) - 10_000
                return {
                    "ok": True,
                    "symbol": symbol,
                    "snapshot_started_at_ms": completed - 1,
                    "snapshot_completed_at_ms": completed,
                    "snapshot_duration_ms": 1,
                    "position_amount": "0.5",
                    "entry_price": "100",
                    "open_orders": [],
                }

        client = FakeClient()
        adapter = AgedSnapshotAdapter(
            api_key="test-key", api_secret="test-secret", client=client
        )
        result = adapter.submit_testnet_protective_exit(
            "BTCUSDT", "stop_loss", "0.5", "99.8", "stop-btc-aged-snapshot"
        )
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "position_snapshot_stale_or_invalid")
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(client.calls, [])

    def test_protective_exit_rejects_trigger_already_crossed_by_mark_price(self):
        class CrossedMarkClient(FakeClient):
            def get(self, path, params):
                if path == "/fapi/v2/account":
                    self.get_calls.append((path, params))
                    return FakeResponse({"canTrade": True, "positions": [
                        {"symbol": "BTCUSDT", "positionAmt": "0.5", "entryPrice": "100"},
                    ]})
                if path == "/fapi/v1/premiumIndex":
                    self.get_calls.append((path, params))
                    return FakeResponse({"symbol": params.get("symbol"), "markPrice": "99.5"})
                return super().get(path, params)

        client = CrossedMarkClient()
        adapter = BinanceFuturesTestnet(
            api_key="test-key", api_secret="test-secret", client=client
        )
        result = adapter.submit_testnet_protective_exit(
            "BTCUSDT", "stop_loss", "0.5", "99.8", "stop-btc-crossed"
        )
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "protective_trigger_already_crossed")
        self.assertEqual(result["mark_price"], "99.5")
        self.assertEqual(client.calls, [])

    def test_protective_exit_rejects_crossed_mark_for_all_position_directions(self):
        cases = (
            ("short-stop", "stop_loss", "-0.5", "100.2", "100.5"),
            ("long-target", "take_profit", "0.5", "101", "101.2"),
            ("short-target", "take_profit", "-0.5", "99", "98.5"),
        )
        for label, exit_kind, position_amount, trigger, mark_price in cases:
            with self.subTest(label=label):
                class PositionMarkClient(FakeClient):
                    def get(self, path, params):
                        if path == "/fapi/v2/account":
                            self.get_calls.append((path, params))
                            return FakeResponse({"canTrade": True, "positions": [
                                {"symbol": "BTCUSDT", "positionAmt": position_amount, "entryPrice": "100"},
                            ]})
                        if path == "/fapi/v1/premiumIndex":
                            self.get_calls.append((path, params))
                            return FakeResponse({"symbol": params.get("symbol"), "markPrice": mark_price})
                        return super().get(path, params)

                client = PositionMarkClient()
                adapter = BinanceFuturesTestnet(
                    api_key="test-key", api_secret="test-secret", client=client
                )
                result = adapter.submit_testnet_protective_exit(
                    "BTCUSDT", exit_kind, "0.5", trigger, f"cross-{label}"
                )
                self.assertFalse(result["accepted"])
                self.assertEqual(result["reason"], "protective_trigger_already_crossed")
                self.assertEqual(result["mark_price"], mark_price)
                self.assertEqual(client.calls, [])

    def test_protective_exit_rejects_mark_exactly_at_trigger(self):
        cases = (
            ("long-stop-equal", "stop_loss", "0.5", "99.8", "99.8"),
            ("short-stop-equal", "stop_loss", "-0.5", "100.2", "100.2"),
            ("long-target-equal", "take_profit", "0.5", "101", "101"),
            ("short-target-equal", "take_profit", "-0.5", "99", "99"),
        )
        for label, exit_kind, position_amount, trigger, mark_price in cases:
            with self.subTest(label=label):
                class EqualMarkClient(FakeClient):
                    def get(self, path, params):
                        if path == "/fapi/v2/account":
                            self.get_calls.append((path, params))
                            return FakeResponse({"canTrade": True, "positions": [
                                {"symbol": "BTCUSDT", "positionAmt": position_amount, "entryPrice": "100"},
                            ]})
                        if path == "/fapi/v1/premiumIndex":
                            self.get_calls.append((path, params))
                            return FakeResponse({"symbol": "BTCUSDT", "markPrice": mark_price})
                        return super().get(path, params)

                client = EqualMarkClient()
                adapter = BinanceFuturesTestnet(
                    api_key="test-key", api_secret="test-secret", client=client
                )
                result = adapter.submit_testnet_protective_exit(
                    "BTCUSDT", exit_kind, "0.5", trigger, f"equal-{label}"
                )
                self.assertFalse(result["accepted"])
                self.assertEqual(result["reason"], "protective_trigger_already_crossed")
                self.assertEqual(result["mark_price"], mark_price)
                self.assertEqual(client.calls, [])

    def test_protective_exit_fails_closed_when_mark_price_is_invalid(self):
        class InvalidMarkClient(FakeClient):
            def get(self, path, params):
                if path == "/fapi/v2/account":
                    self.get_calls.append((path, params))
                    return FakeResponse({"canTrade": True, "positions": [
                        {"symbol": "BTCUSDT", "positionAmt": "0.5", "entryPrice": "100"},
                    ]})
                if path == "/fapi/v1/premiumIndex":
                    self.get_calls.append((path, params))
                    return FakeResponse({"symbol": params.get("symbol"), "markPrice": "NaN"})
                return super().get(path, params)

        client = InvalidMarkClient()
        adapter = BinanceFuturesTestnet(
            api_key="test-key", api_secret="test-secret", client=client
        )
        result = adapter.submit_testnet_protective_exit(
            "BTCUSDT", "stop_loss", "0.5", "99.8", "stop-btc-badmark"
        )
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "mark_price_preflight_failed")
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(client.calls, [])

    def test_protective_exit_fails_closed_when_mark_symbol_mismatches(self):
        class MismatchedMarkClient(FakeClient):
            def get(self, path, params):
                if path == "/fapi/v2/account":
                    self.get_calls.append((path, params))
                    return FakeResponse({"canTrade": True, "positions": [
                        {"symbol": "BTCUSDT", "positionAmt": "0.5", "entryPrice": "100"},
                    ]})
                if path == "/fapi/v1/premiumIndex":
                    self.get_calls.append((path, params))
                    return FakeResponse({"symbol": "ETHUSDT", "markPrice": "100"})
                return super().get(path, params)

        client = MismatchedMarkClient()
        adapter = BinanceFuturesTestnet(
            api_key="test-key", api_secret="test-secret", client=client
        )
        result = adapter.submit_testnet_protective_exit(
            "BTCUSDT", "stop_loss", "0.5", "99.8", "stop-btc-wrongmarksymbol"
        )
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "mark_price_preflight_failed")
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(client.calls, [])

    def test_protective_exit_fails_closed_on_unexpected_mark_client_exception(self):
        class RaisingMarkClient(FakeClient):
            def get(self, path, params):
                if path == "/fapi/v2/account":
                    self.get_calls.append((path, params))
                    return FakeResponse({"canTrade": True, "positions": [
                        {"symbol": "BTCUSDT", "positionAmt": "0.5", "entryPrice": "100"},
                    ]})
                if path == "/fapi/v1/premiumIndex":
                    self.get_calls.append((path, params))
                    raise RuntimeError("unexpected client failure")
                return super().get(path, params)

        client = RaisingMarkClient()
        adapter = BinanceFuturesTestnet(
            api_key="test-key", api_secret="test-secret", client=client
        )
        result = adapter.submit_testnet_protective_exit(
            "BTCUSDT", "stop_loss", "0.5", "99.8", "stop-btc-mark-exception"
        )
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "mark_price_preflight_failed")
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(client.calls, [])

    def test_protective_exit_rejects_partial_quantity_and_nonfinite_position(self):
        class PositionClient(FakeClient):
            def __init__(self, position_amount):
                super().__init__()
                self.position_amount = position_amount
            def get(self, path, params):
                if path == "/fapi/v2/account":
                    self.get_calls.append((path, params))
                    return FakeResponse({"canTrade": True, "positions": [
                        {"symbol": "BTCUSDT", "positionAmt": self.position_amount, "entryPrice": "100"},
                    ]})
                return super().get(path, params)

        partial_client = PositionClient("0.5")
        partial_adapter = BinanceFuturesTestnet(
            api_key="test-key", api_secret="test-secret", client=partial_client
        )
        partial = partial_adapter.submit_testnet_protective_exit(
            "BTCUSDT", "stop_loss", "0.4", "99.8", "stop-btc-partial"
        )
        self.assertFalse(partial["accepted"])
        self.assertEqual(partial["reason"], "protective_exit_must_match_full_position_quantity")
        self.assertEqual(partial_client.calls, [])

        invalid_client = PositionClient("NaN")
        invalid_adapter = BinanceFuturesTestnet(
            api_key="test-key", api_secret="test-secret", client=invalid_client
        )
        invalid = invalid_adapter.submit_testnet_protective_exit(
            "BTCUSDT", "stop_loss", "0.5", "99.8", "stop-btc-nan"
        )
        self.assertFalse(invalid["accepted"])
        self.assertEqual(invalid["reason"], "position_snapshot_failed")
        self.assertEqual(invalid_client.calls, [])

    def test_protective_exit_rejects_stale_or_inconsistent_snapshot_window(self):
        client = FakeClient()
        adapter = BinanceFuturesTestnet(
            api_key="test-key", api_secret="test-secret", client=client
        )
        base_snapshot = {
            "ok": True,
            "snapshot_started_at_ms": 1000,
            "snapshot_completed_at_ms": 4000,
            "snapshot_duration_ms": 3000,
            "position_amount": "0.5",
            "entry_price": "100",
            "open_orders": [],
        }
        adapter.testnet_position_snapshot = lambda symbol: dict(base_snapshot)
        stale = adapter.submit_testnet_protective_exit(
            "BTCUSDT", "stop_loss", "0.5", "99.8", "stop-btc-stale"
        )
        self.assertFalse(stale["accepted"])
        self.assertEqual(stale["reason"], "position_snapshot_stale_or_invalid")
        self.assertTrue(stale["requires_reconciliation"])

        inconsistent_snapshot = dict(
            base_snapshot,
            snapshot_completed_at_ms=1500,
            snapshot_duration_ms=400,
        )
        adapter.testnet_position_snapshot = lambda symbol: dict(inconsistent_snapshot)
        inconsistent = adapter.submit_testnet_protective_exit(
            "BTCUSDT", "stop_loss", "0.5", "99.8", "stop-btc-inconsistent"
        )
        self.assertFalse(inconsistent["accepted"])
        self.assertEqual(inconsistent["reason"], "position_snapshot_stale_or_invalid")
        self.assertTrue(inconsistent["requires_reconciliation"])
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


    def test_market_order_timeout_is_ambiguous_and_never_retried(self):
        class TimeoutClient(FakeClient):
            def post(self, path, params):
                self.calls.append((path, params))
                raise httpx.ReadTimeout("simulated timeout")

        client = TimeoutClient()
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        result = adapter.submit_testnet_market_order(
            self.intent(), "btc-timeout-0001", ExecutionConfig(mode="testnet")
        )
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "testnet_order_outcome_unknown")
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0][0], "/fapi/v1/order")

    def test_protective_exit_timeout_is_ambiguous_and_never_retried(self):
        class TimeoutPositionClient(FakeClient):
            def get(self, path, params):
                if path == "/fapi/v2/account":
                    self.get_calls.append((path, params))
                    return FakeResponse({"canTrade": True, "positions": [
                        {"symbol": "BTCUSDT", "positionAmt": "0.5", "entryPrice": "100"},
                    ]})
                return super().get(path, params)
            def post(self, path, params):
                self.calls.append((path, params))
                raise httpx.ReadTimeout("simulated timeout")

        client = TimeoutPositionClient()
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        result = adapter.submit_testnet_protective_exit(
            "BTCUSDT", "stop_loss", "0.5", "99.8", "btc-stop-timeout"
        )
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "protective_exit_outcome_unknown")
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0][0], "/fapi/v1/order")

    def test_protective_exit_unexpected_post_exception_is_ambiguous_and_never_retried(self):
        class UnexpectedPostClient(FakeClient):
            def get(self, path, params):
                if path == "/fapi/v2/account":
                    self.get_calls.append((path, params))
                    return FakeResponse({"canTrade": True, "positions": [
                        {"symbol": "BTCUSDT", "positionAmt": "0.5", "entryPrice": "100"},
                    ]})
                return super().get(path, params)

            def post(self, path, params):
                self.calls.append((path, params))
                raise RuntimeError("unexpected custom adapter failure")

        client = UnexpectedPostClient()
        adapter = BinanceFuturesTestnet(
            api_key="test-key", api_secret="test-secret", client=client
        )
        result = adapter.submit_testnet_protective_exit(
            "BTCUSDT", "stop_loss", "0.5", "99.8", "btc-stop-runtime-error"
        )
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "protective_exit_outcome_unknown")
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(result["exception_type"], "RuntimeError")
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0][0], "/fapi/v1/order")

    def test_malformed_post_response_requires_reconciliation_without_retry(self):
        class MalformedResponse(FakeResponse):
            def json(self):
                raise ValueError("malformed response")
        class MalformedClient(FakeClient):
            def post(self, path, params):
                self.calls.append((path, params))
                return MalformedResponse()

        client = MalformedClient()
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        result = adapter.submit_testnet_market_order(
            self.intent(), "btc-malformed-0001", ExecutionConfig(mode="testnet")
        )
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "testnet_order_outcome_unknown")
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(len(client.calls), 1)

    def test_cancel_order_uses_signed_specific_testnet_client_id(self):
        client = FakeClient()
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        result = adapter.cancel_testnet_order("BTCUSDT", "btc-stop-0001")
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["mode"], "testnet_order_cancelled")
        self.assertEqual(client.calls[0][0], "/fapi/v1/order")
        self.assertEqual(client.calls[0][1]["origClientOrderId"], "btc-stop-0001")
        self.assertIn("signature", client.calls[0][1])

    def test_cancel_order_timeout_is_ambiguous_and_never_retried(self):
        class TimeoutCancelClient(FakeClient):
            def delete(self, path, params):
                self.calls.append((path, params))
                raise httpx.ReadTimeout(
                    "cancel response timed out",
                    request=httpx.Request("DELETE", "https://testnet.binancefuture.com/fapi/v1/order"),
                )

        client = TimeoutCancelClient()
        adapter = BinanceFuturesTestnet(
            api_key="test-key", api_secret="test-secret", client=client
        )
        result = adapter.cancel_testnet_order("BTCUSDT", "btc-stop-timeout")
        self.assertFalse(result["cancelled"])
        self.assertEqual(result["reason"], "testnet_cancel_outcome_unknown")
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(len(client.calls), 1)

    def test_cancel_order_http_404_requires_reconciliation_without_retry(self):
        class MissingCancelClient(FakeClient):
            def delete(self, path, params):
                self.calls.append((path, params))
                request = httpx.Request(
                    "DELETE", "https://testnet.binancefuture.com/fapi/v1/order"
                )
                response = httpx.Response(404, json={
                    "code": -2011, "msg": "Unknown order sent."
                }, request=request)
                raise httpx.HTTPStatusError(
                    "cancel response returned 404", request=request, response=response
                )

        client = MissingCancelClient()
        adapter = BinanceFuturesTestnet(
            api_key="test-key", api_secret="test-secret", client=client
        )
        result = adapter.cancel_testnet_order("BTCUSDT", "btc-stop-missing")
        self.assertFalse(result["cancelled"])
        self.assertEqual(
            result["reason"], "testnet_cancel_http_outcome_requires_reconciliation"
        )
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(result["http_status"], 404)
        self.assertEqual(len(client.calls), 1)

    def test_cancel_order_unexpected_client_exception_fails_closed_without_retry(self):
        class UnexpectedFailureClient(FakeClient):
            def delete(self, path, params):
                self.calls.append((path, params))
                raise RuntimeError("unexpected client failure after request dispatch")

        client = UnexpectedFailureClient()
        adapter = BinanceFuturesTestnet(
            api_key="test-key", api_secret="test-secret", client=client
        )
        result = adapter.cancel_testnet_order("BTCUSDT", "btc-stop-runtime-error")
        self.assertFalse(result["cancelled"])
        self.assertEqual(
            result["reason"], "testnet_cancel_unexpected_error_requires_reconciliation"
        )
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(result["exception_type"], "RuntimeError")
        self.assertEqual(len(client.calls), 1)

    def test_cancel_order_nonterminal_exchange_status_requires_reconciliation(self):
        class StillActiveCancelClient(FakeClient):
            def delete(self, path, params):
                self.calls.append((path, params))
                return FakeResponse({
                    "symbol": params.get("symbol"),
                    "clientOrderId": params.get("origClientOrderId"),
                    "orderId": 12345,
                    "status": "PARTIALLY_FILLED",
                })

        client = StillActiveCancelClient()
        adapter = BinanceFuturesTestnet(
            api_key="test-key", api_secret="test-secret", client=client
        )
        result = adapter.cancel_testnet_order("BTCUSDT", "btc-stop-partial")
        self.assertFalse(result["cancelled"])
        self.assertEqual(
            result["reason"], "testnet_cancel_not_confirmed_requires_reconciliation"
        )
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(result["status"], "PARTIALLY_FILLED")
        self.assertEqual(len(client.calls), 1)

    def test_cancel_order_rejects_mismatched_exchange_identity(self):
        class MismatchedClient(FakeClient):
            def delete(self, path, params):
                self.calls.append((path, params))
                return FakeResponse({
                    "symbol": "ETHUSDT",
                    "clientOrderId": "different-client-id",
                    "orderId": 12345,
                    "status": "CANCELED",
                })

        client = MismatchedClient()
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        result = adapter.cancel_testnet_order("BTCUSDT", "btc-stop-0001")
        self.assertFalse(result["cancelled"])
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(result["reason"], "unexpected_or_mismatched_testnet_cancel_response")
        self.assertEqual(len(client.calls), 1)

    def test_cancel_order_rejects_invalid_id_without_request(self):
        client = FakeClient()
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        result = adapter.cancel_testnet_order("BTCUSDT", "bad client id")
        self.assertFalse(result["cancelled"])
        self.assertEqual(result["reason"], "invalid_client_order_id")
        self.assertEqual(client.calls, [])


    def test_query_order_unexpected_client_exception_requires_reconciliation(self):
        class BrokenQueryClient(FakeClient):
            def get(self, path, params):
                if path == "/fapi/v1/order":
                    raise RuntimeError("unexpected adapter failure")
                return super().get(path, params)

        client = BrokenQueryClient()
        adapter = BinanceFuturesTestnet(
            api_key="test-key", api_secret="test-secret", client=client
        )
        result = adapter.query_testnet_order("BTCUSDT", "paper-test-0001")
        self.assertFalse(result["found"])
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(
            result["reason"],
            "testnet_order_query_unexpected_error_requires_reconciliation",
        )
        self.assertEqual(result["exception_type"], "RuntimeError")
        self.assertEqual(client.calls, [])

    def test_query_order_rejects_status_quantity_contradictions(self):
        base = {
            "symbol": "BTCUSDT",
            "clientOrderId": "paper-test-0001",
            "orderId": 12345,
            "origQty": "0.5",
            "avgPrice": "100.1",
        }
        cases = [
            (
                {**base, "status": "NEW", "executedQty": "0.1"},
                "new_testnet_order_has_execution_requires_reconciliation",
            ),
            (
                {**base, "status": "PARTIALLY_FILLED", "executedQty": "0"},
                "partially_filled_testnet_order_quantity_mismatch",
            ),
            (
                {**base, "status": "PARTIALLY_FILLED", "executedQty": "0.5"},
                "partially_filled_testnet_order_quantity_mismatch",
            ),
        ]

        class StatusQuantityClient(FakeClient):
            def __init__(self, payload):
                super().__init__()
                self.payload = payload

            def get(self, path, params):
                if path == "/fapi/v1/order":
                    self.get_calls.append((path, params))
                    return FakeResponse(self.payload)
                return super().get(path, params)

        for payload, reason in cases:
            with self.subTest(status=payload["status"], executed=payload["executedQty"]):
                client = StatusQuantityClient(payload)
                adapter = BinanceFuturesTestnet(
                    api_key="test-key", api_secret="test-secret", client=client
                )
                result = adapter.query_testnet_order("BTCUSDT", "paper-test-0001")
                self.assertFalse(result["found"])
                self.assertTrue(result["requires_reconciliation"])
                self.assertEqual(result["reason"], reason)
                self.assertEqual(
                    len([call for call in client.get_calls if call[0] == "/fapi/v1/order"]),
                    1,
                )
                self.assertEqual(client.calls, [])

    def test_query_order_rejects_invalid_or_inconsistent_quantities(self):
        class InvalidOrderClient(FakeClient):
            def __init__(self, payload):
                super().__init__()
                self.payload = payload

            def get(self, path, params):
                if path == "/fapi/v1/order":
                    self.get_calls.append((path, params))
                    return FakeResponse(self.payload)
                return super().get(path, params)

        base = {
            "symbol": "BTCUSDT",
            "clientOrderId": "paper-test-0001",
            "orderId": 12345,
            "status": "FILLED",
            "origQty": "0.5",
            "executedQty": "0.5",
            "avgPrice": "100.1",
        }
        cases = [
            ({**base, "executedQty": "NaN"}, "invalid_testnet_order_numeric_fields"),
            ({**base, "executedQty": "0.6"}, "invalid_testnet_order_numeric_fields"),
            ({**base, "executedQty": "0.4"}, "filled_testnet_order_quantity_mismatch"),
            ({**base, "origQty": "Infinity"}, "invalid_testnet_order_numeric_fields"),
        ]
        for payload, reason in cases:
            with self.subTest(payload=payload):
                client = InvalidOrderClient(payload)
                adapter = BinanceFuturesTestnet(
                    api_key="test-key", api_secret="test-secret", client=client
                )
                result = adapter.query_testnet_order("BTCUSDT", "paper-test-0001")
                self.assertFalse(result["found"])
                self.assertTrue(result["requires_reconciliation"])
                self.assertEqual(result["reason"], reason)
                self.assertEqual(client.calls, [])


    def test_query_order_rejects_invalid_exchange_order_ids(self):
        class InvalidOrderIdClient(FakeClient):
            def __init__(self, order_id):
                super().__init__()
                self.order_id = order_id

            def get(self, path, params):
                if path == "/fapi/v1/order":
                    self.get_calls.append((path, params))
                    return FakeResponse({
                        "symbol": "BTCUSDT",
                        "clientOrderId": "paper-test-0001",
                        "orderId": self.order_id,
                        "status": "FILLED",
                        "origQty": "0.5",
                        "executedQty": "0.5",
                        "avgPrice": "100.1",
                    })
                return super().get(path, params)

        for order_id in (True, False, 0, -1, "12.3", "not-an-id", "", None):
            with self.subTest(order_id=order_id):
                client = InvalidOrderIdClient(order_id)
                adapter = BinanceFuturesTestnet(
                    api_key="test-key", api_secret="test-secret", client=client
                )
                result = adapter.query_testnet_order("BTCUSDT", "paper-test-0001")
                self.assertFalse(result["found"])
                self.assertTrue(result["requires_reconciliation"])
                self.assertEqual(
                    result["reason"], "unexpected_or_mismatched_testnet_order_response"
                )
                self.assertEqual(client.calls, [])


    def test_position_snapshot_rejects_missing_position_fields(self):
        for missing_field in ("positionAmt", "entryPrice"):
            with self.subTest(missing_field=missing_field):
                class MissingPositionFieldClient(FakeClient):
                    def get(self, path, params):
                        if path == "/fapi/v2/account":
                            position = {"symbol": "BTCUSDT", "positionAmt": "0", "entryPrice": "0"}
                            position.pop(missing_field)
                            return FakeResponse({"canTrade": True, "positions": [position]})
                        return super().get(path, params)

                client = MissingPositionFieldClient()
                adapter = BinanceFuturesTestnet(
                    api_key="test-key", api_secret="test-secret", client=client
                )
                result = adapter.testnet_position_snapshot("BTCUSDT")
                self.assertFalse(result["ok"])
                self.assertEqual(result["reason"], "invalid_testnet_position_values")
                self.assertFalse(any(path == "/fapi/v1/openOrders" for path, _ in client.get_calls))

    def test_position_snapshot_rejects_open_order_executed_quantity_above_original(self):
        class OverfilledOrderClient(FakeClient):
            def get(self, path, params):
                if path == "/fapi/v1/openOrders":
                    self.get_calls.append((path, params))
                    return FakeResponse([{
                        "orderId": 99, "symbol": "BTCUSDT", "clientOrderId": "stop-0001",
                        "side": "SELL", "type": "STOP_MARKET", "status": "NEW",
                        "reduceOnly": True, "stopPrice": "99.8", "origQty": "0.5",
                        "executedQty": "0.6",
                    }])
                return super().get(path, params)

        client = OverfilledOrderClient()
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        result = adapter.testnet_position_snapshot("BTCUSDT")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "invalid_testnet_open_order_numeric_fields")
        self.assertEqual(client.calls, [])



    def test_market_order_rejects_malformed_exchange_order_id(self):
        class MalformedOrderIdClient(FakeClient):
            def post(self, path, params):
                self.calls.append((path, params))
                return FakeResponse({
                    "symbol": params.get("symbol"),
                    "clientOrderId": params.get("newClientOrderId"),
                    "orderId": True,
                    "status": "NEW",
                })

        client = MalformedOrderIdClient()
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        result = adapter.submit_testnet_market_order(
            self.intent(), "btc-bad-order-id", ExecutionConfig(mode="testnet")
        )
        self.assertFalse(result["accepted"])
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(result["reason"], "unexpected_testnet_order_response")
        self.assertEqual(len(client.calls), 1)



    def test_cancel_order_rejects_malformed_exchange_order_id(self):
        class MalformedCancelIdClient(FakeClient):
            def delete(self, path, params):
                self.calls.append((path, params))
                return FakeResponse({
                    "symbol": params.get("symbol"),
                    "clientOrderId": params.get("origClientOrderId"),
                    "orderId": True,
                    "status": "CANCELED",
                })

        client = MalformedCancelIdClient()
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        result = adapter.cancel_testnet_order("BTCUSDT", "btc-stop-0001")
        self.assertFalse(result["cancelled"])
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(result["reason"], "unexpected_or_mismatched_testnet_cancel_response")
        self.assertEqual(len(client.calls), 1)

    def test_protective_exit_rejects_mismatched_exchange_echo_fields(self):
        cases = [
            ("side", "BUY"),
            ("type", "LIMIT"),
            ("reduceOnly", False),
            ("workingType", "CONTRACT_PRICE"),
            ("origQty", "0.4"),
            ("stopPrice", "99.7"),
        ]
        for field, wrong_value in cases:
            with self.subTest(field=field):
                class MismatchedEchoClient(FakeClient):
                    def get(self, path, params):
                        if path == "/fapi/v2/account":
                            self.get_calls.append((path, params))
                            return FakeResponse({"canTrade": True, "positions": [
                                {"symbol": "BTCUSDT", "positionAmt": "0.5", "entryPrice": "100"},
                            ]})
                        return super().get(path, params)

                    def post(self, path, params):
                        self.calls.append((path, params))
                        payload = {
                            "symbol": params.get("symbol"),
                            "clientOrderId": params.get("newClientOrderId"),
                            "orderId": 12345,
                            "status": "NEW",
                            "side": params.get("side"),
                            "type": params.get("type"),
                            "reduceOnly": True,
                            "workingType": params.get("workingType"),
                            "origQty": params.get("quantity"),
                            "stopPrice": params.get("stopPrice"),
                        }
                        payload[field] = wrong_value
                        return FakeResponse(payload)

                client = MismatchedEchoClient()
                adapter = BinanceFuturesTestnet(
                    api_key="test-key", api_secret="test-secret", client=client
                )
                result = adapter.submit_testnet_protective_exit(
                    "BTCUSDT", "stop_loss", "0.5", "99.8", "btc-echo-check"
                )
                self.assertFalse(result["accepted"])
                self.assertTrue(result["requires_reconciliation"])
                self.assertEqual(result["reason"], "unexpected_testnet_order_response")
                self.assertEqual(len(client.calls), 1)

    def test_protective_exit_already_filled_on_submit_requires_reconciliation(self):
        class FilledOnSubmitClient(FakeClient):
            def get(self, path, params):
                if path == "/fapi/v2/account":
                    self.get_calls.append((path, params))
                    return FakeResponse({"canTrade": True, "positions": [
                        {"symbol": "BTCUSDT", "positionAmt": "0.5", "entryPrice": "100"},
                    ]})
                return super().get(path, params)

            def post(self, path, params):
                self.calls.append((path, params))
                return FakeResponse({
                    "symbol": params.get("symbol"),
                    "clientOrderId": params.get("newClientOrderId"),
                    "orderId": 12345,
                    "status": "FILLED",
                    "side": params.get("side"),
                    "type": params.get("type"),
                    "reduceOnly": True,
                    "workingType": params.get("workingType"),
                    "origQty": params.get("quantity"),
                    "stopPrice": params.get("stopPrice"),
                })

        client = FilledOnSubmitClient()
        adapter = BinanceFuturesTestnet(
            api_key="test-key", api_secret="test-secret", client=client
        )
        result = adapter.submit_testnet_protective_exit(
            "BTCUSDT", "stop_loss", "0.5", "99.8", "btc-already-filled"
        )
        self.assertFalse(result["accepted"])
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(
            result["reason"], "protective_exit_submission_state_requires_reconciliation"
        )
        self.assertEqual(result["status"], "FILLED")
        self.assertEqual(len(client.calls), 1)

    def test_protective_exit_rejects_malformed_exchange_order_id(self):
        class PositionClient(FakeClient):
            def get(self, path, params):
                if path == "/fapi/v2/account":
                    self.get_calls.append((path, params))
                    return FakeResponse({"canTrade": True, "positions": [
                        {"symbol": "BTCUSDT", "positionAmt": "0.5", "entryPrice": "100"},
                    ]})
                return super().get(path, params)

            def post(self, path, params):
                self.calls.append((path, params))
                return FakeResponse({
                    "symbol": params.get("symbol"),
                    "clientOrderId": params.get("newClientOrderId"),
                    "orderId": True,
                    "status": "NEW",
                })

        client = PositionClient()
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        result = adapter.submit_testnet_protective_exit(
            "BTCUSDT", "stop_loss", "0.5", "99.8", "btc-stop-bad-id"
        )
        self.assertFalse(result["accepted"])
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(result["reason"], "unexpected_testnet_order_response")
        self.assertEqual(len(client.calls), 1)



    def test_position_snapshot_rejects_nonpositive_open_order_id(self):
        client = FakeClient()
        original_get = client.get

        def malformed_get(path, params):
            if path == "/fapi/v1/openOrders":
                return FakeResponse([{
                    "orderId": 0, "symbol": "BTCUSDT", "clientOrderId": "stop-0001",
                    "side": "SELL", "type": "STOP_MARKET", "status": "NEW",
                    "reduceOnly": True, "stopPrice": "99.8", "origQty": "0.5",
                    "executedQty": "0",
                }])
            return original_get(path, params)

        client.get = malformed_get
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        result = adapter.testnet_position_snapshot("BTCUSDT")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "invalid_testnet_open_order_identity")

    def test_position_snapshot_rejects_new_order_with_nonzero_execution(self):
        client = FakeClient()
        original_get = client.get

        def malformed_get(path, params):
            if path == "/fapi/v1/openOrders":
                return FakeResponse([{
                    "orderId": 124, "symbol": "BTCUSDT", "clientOrderId": "new-with-fill",
                    "side": "SELL", "type": "STOP_MARKET", "status": "NEW",
                    "reduceOnly": True, "stopPrice": "99.8", "origQty": "0.5",
                    "executedQty": "0.1",
                }])
            return original_get(path, params)

        client.get = malformed_get
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        result = adapter.testnet_position_snapshot("BTCUSDT")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "invalid_testnet_open_order_numeric_fields")

    def test_position_snapshot_rejects_partially_filled_order_with_zero_execution(self):
        client = FakeClient()
        original_get = client.get

        def malformed_get(path, params):
            if path == "/fapi/v1/openOrders":
                return FakeResponse([{
                    "orderId": 125, "symbol": "BTCUSDT", "clientOrderId": "partial-without-fill",
                    "side": "SELL", "type": "STOP_MARKET", "status": "PARTIALLY_FILLED",
                    "reduceOnly": True, "stopPrice": "99.8", "origQty": "0.5",
                    "executedQty": "0",
                }])
            return original_get(path, params)

        client.get = malformed_get
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        result = adapter.testnet_position_snapshot("BTCUSDT")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "invalid_testnet_open_order_numeric_fields")

    def test_position_snapshot_rejects_non_open_status_in_open_orders(self):
        client = FakeClient()
        original_get = client.get

        def malformed_get(path, params):
            if path == "/fapi/v1/openOrders":
                return FakeResponse([{
                    "orderId": 123, "symbol": "BTCUSDT", "clientOrderId": "stop-0001",
                    "side": "SELL", "type": "STOP_MARKET", "status": "FILLED",
                    "reduceOnly": True, "stopPrice": "99.8", "origQty": "0.5",
                    "executedQty": "0.5",
                }])
            return original_get(path, params)

        client.get = malformed_get
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        result = adapter.testnet_position_snapshot("BTCUSDT")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "invalid_testnet_open_order_status")



    def test_protective_exit_snapshot_exception_fails_closed(self):
        class SnapshotExceptionClient(FakeClient):
            def get(self, path, params):
                if path == "/fapi/v2/account":
                    self.get_calls.append((path, params))
                    raise httpx.TimeoutException("snapshot timed out")
                return super().get(path, params)

        client = SnapshotExceptionClient()
        adapter = BinanceFuturesTestnet(
            api_key="test-key", api_secret="test-secret", client=client
        )
        result = adapter.submit_testnet_protective_exit(
            "BTCUSDT", "stop_loss", "0.5", "99.8", "stop-snapshot-timeout"
        )
        self.assertFalse(result["accepted"])
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(
            result["reason"], "position_snapshot_failed_requires_reconciliation"
        )
        self.assertEqual(result["exception_type"], "TimeoutException")
        self.assertEqual(client.calls, [])

    def test_protective_exit_refuses_client_id_already_in_open_order_snapshot(self):
        class ExistingClientOrderClient(FakeClient):
            def get(self, path, params):
                if path == "/fapi/v2/account":
                    self.get_calls.append((path, params))
                    return FakeResponse({"canTrade": True, "positions": [
                        {"symbol": "BTCUSDT", "positionAmt": "0.5", "entryPrice": "100"},
                    ]})
                if path == "/fapi/v1/openOrders":
                    self.get_calls.append((path, params))
                    return FakeResponse([{
                        "orderId": 99, "symbol": "BTCUSDT", "clientOrderId": "stop-0001",
                        "side": "SELL", "type": "STOP_MARKET", "status": "NEW",
                        "reduceOnly": True, "stopPrice": "99.8", "origQty": "0.5",
                        "executedQty": "0",
                    }])
                return super().get(path, params)

        client = ExistingClientOrderClient()
        adapter = BinanceFuturesTestnet(
            api_key="test-key", api_secret="test-secret", client=client
        )
        result = adapter.submit_testnet_protective_exit(
            "BTCUSDT", "stop_loss", "0.5", "99.8", "stop-0001"
        )
        self.assertFalse(result["accepted"])
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(
            result["reason"],
            "protective_exit_client_id_already_open_requires_reconciliation",
        )
        self.assertEqual(result["client_order_id"], "stop-0001")
        self.assertEqual(client.calls, [])
        self.assertFalse(any(path == "/fapi/v1/premiumIndex" for path, _ in client.get_calls))

    def test_protective_exit_refuses_second_open_stop_loss_for_same_position(self):
        class ExistingStopClient(FakeClient):
            def get(self, path, params):
                if path == "/fapi/v2/account":
                    self.get_calls.append((path, params))
                    return FakeResponse({"canTrade": True, "positions": [
                        {"symbol": "BTCUSDT", "positionAmt": "0.5", "entryPrice": "100"},
                    ]})
                if path == "/fapi/v1/openOrders":
                    self.get_calls.append((path, params))
                    return FakeResponse([{
                        "orderId": 99, "symbol": "BTCUSDT", "clientOrderId": "existing-stop",
                        "side": "SELL", "type": "STOP_MARKET", "status": "NEW",
                        "reduceOnly": True, "stopPrice": "99.7", "origQty": "0.5",
                        "executedQty": "0",
                    }])
                return super().get(path, params)

        client = ExistingStopClient()
        adapter = BinanceFuturesTestnet(
            api_key="test-key", api_secret="test-secret", client=client
        )
        result = adapter.submit_testnet_protective_exit(
            "BTCUSDT", "stop_loss", "0.5", "99.8", "new-stop-id"
        )
        self.assertFalse(result["accepted"])
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(
            result["reason"], "protective_exit_existing_order_requires_reconciliation"
        )
        self.assertEqual(result["existing_client_order_ids"], ["existing-stop"])
        self.assertEqual(client.calls, [])
        self.assertFalse(any(path == "/fapi/v1/premiumIndex" for path, _ in client.get_calls))

    def test_protective_exit_refuses_partially_filled_existing_stop_loss(self):
        class PartiallyFilledStopClient(FakeClient):
            def get(self, path, params):
                if path == "/fapi/v2/account":
                    self.get_calls.append((path, params))
                    return FakeResponse({"canTrade": True, "positions": [
                        {"symbol": "BTCUSDT", "positionAmt": "0.5", "entryPrice": "100"},
                    ]})
                if path == "/fapi/v1/openOrders":
                    self.get_calls.append((path, params))
                    return FakeResponse([{
                        "orderId": 100, "symbol": "BTCUSDT", "clientOrderId": "partially-filled-stop",
                        "side": "SELL", "type": "STOP_MARKET", "status": "PARTIALLY_FILLED",
                        "reduceOnly": True, "stopPrice": "99.7", "origQty": "0.5",
                        "executedQty": "0.1",
                    }])
                return super().get(path, params)

        client = PartiallyFilledStopClient()
        adapter = BinanceFuturesTestnet(
            api_key="test-key", api_secret="test-secret", client=client
        )
        result = adapter.submit_testnet_protective_exit(
            "BTCUSDT", "stop_loss", "0.5", "99.8", "new-stop-after-partial"
        )
        self.assertFalse(result["accepted"])
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(
            result["reason"], "protective_exit_existing_order_requires_reconciliation"
        )
        self.assertEqual(
            result["existing_client_order_ids"], ["partially-filled-stop"]
        )
        self.assertEqual(client.calls, [])
        self.assertFalse(any(path == "/fapi/v1/premiumIndex" for path, _ in client.get_calls))

    def test_protective_exit_duplicate_client_id_error_requires_reconciliation(self):
        class DuplicateClientIdClient(FakeClient):
            def get(self, path, params):
                if path == "/fapi/v2/account":
                    self.get_calls.append((path, params))
                    return FakeResponse({"canTrade": True, "positions": [
                        {"symbol": "BTCUSDT", "positionAmt": "0.5", "entryPrice": "100"},
                    ]})
                return super().get(path, params)

            def post(self, path, params):
                self.calls.append((path, params))
                response = httpx.Response(
                    400,
                    json={"code": -4116, "msg": "Client order ID is not unique."},
                    request=httpx.Request("POST", "https://testnet.binancefuture.com/fapi/v1/order"),
                )
                raise httpx.HTTPStatusError(
                    "duplicate client order ID", request=response.request, response=response
                )

        client = DuplicateClientIdClient()
        adapter = BinanceFuturesTestnet(
            api_key="test-key", api_secret="test-secret", client=client
        )
        result = adapter.submit_testnet_protective_exit(
            "BTCUSDT", "stop_loss", "0.5", "99.8", "btc-recovery-sl"
        )
        self.assertFalse(result["accepted"])
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(
            result["reason"],
            "protective_exit_client_id_conflict_requires_reconciliation",
        )
        self.assertEqual(result["client_order_id"], "btc-recovery-sl")
        self.assertEqual(len(client.calls), 1)

    def test_account_risk_metrics_network_failure_fails_closed(self):
        class FailingAccountClient(FakeClient):
            def get(self, path, params):
                if path == "/fapi/v2/account":
                    self.get_calls.append((path, params))
                    raise httpx.ReadTimeout("simulated account timeout")
                return super().get(path, params)

        client = FailingAccountClient()
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        result = adapter.account_risk_metrics("BTCUSDT")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "account_risk_metrics_request_failed")
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(result["detail"]["exception_type"], "ReadTimeout")
        self.assertEqual(len(client.get_calls), 1)
        self.assertEqual(client.calls, [])

    def test_account_risk_metrics_rejects_notional_outside_float_range(self):
        class HugeNotionalClient(FakeClient):
            def get(self, path, params):
                if path == "/fapi/v2/account":
                    self.get_calls.append((path, params))
                    return FakeResponse({
                        "canTrade": True,
                        "positions": [{
                            "symbol": "BTCUSDT",
                            "positionAmt": "1",
                            "notional": "1e10000",
                        }],
                    })
                return super().get(path, params)

        client = HugeNotionalClient()
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        result = adapter.account_risk_metrics("BTCUSDT")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "risk_metrics_out_of_float_range")
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(client.calls, [])

    def test_account_risk_metrics_missing_open_position_notional_fails_closed(self):
        class MissingNotionalClient(FakeClient):
            def get(self, path, params):
                if path == "/fapi/v2/account":
                    self.get_calls.append((path, params))
                    return FakeResponse({
                        "canTrade": True,
                        "positions": [{
                            "symbol": "BTCUSDT",
                            "positionAmt": "0.1",
                        }],
                    })
                return super().get(path, params)

        client = MissingNotionalClient()
        adapter = BinanceFuturesTestnet(api_key="test-key", api_secret="test-secret", client=client)
        result = adapter.account_risk_metrics("BTCUSDT")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "position_notional_missing")
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(client.calls, [])


    def test_account_risk_metrics_missing_position_amount_fails_closed(self):
        class MissingPositionAmountClient(FakeClient):
            def get(self, path, params):
                if path == "/fapi/v2/account":
                    self.get_calls.append((path, params))
                    return FakeResponse({
                        "canTrade": True,
                        "positions": [{
                            "symbol": "BTCUSDT",
                            "notional": "2500",
                        }],
                    })
                return super().get(path, params)

        client = MissingPositionAmountClient()
        adapter = BinanceFuturesTestnet(
            api_key="test-key", api_secret="test-secret", client=client
        )
        result = adapter.account_risk_metrics("BTCUSDT")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "invalid_position_record")
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(client.calls, [])

if __name__ == "__main__":
    unittest.main()
