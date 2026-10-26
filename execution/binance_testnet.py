"""Binance USD-M futures TESTNET adapter skeleton.

This adapter only targets the Binance futures testnet host. It does not contain
or select a production endpoint. API credentials must be supplied through
environment variables and must never be committed to the repository.
"""
import hashlib
from decimal import Decimal, InvalidOperation, ROUND_DOWN
import hmac
import os
import time
from urllib.parse import urlencode

import httpx

from execution.safety import ExecutionConfig, OrderIntent, validate_order_intent

TESTNET_BASE_URL = "https://testnet.binancefuture.com"


class BinanceTestnetConfigurationError(RuntimeError):
    pass


class BinanceFuturesTestnet:
    def __init__(self, api_key=None, api_secret=None, client=None):
        self.api_key = api_key if api_key is not None else os.getenv("BINANCE_TESTNET_API_KEY", "")
        self.api_secret = api_secret if api_secret is not None else os.getenv("BINANCE_TESTNET_API_SECRET", "")
        if not self.api_key or not self.api_secret:
            raise BinanceTestnetConfigurationError(
                "Set BINANCE_TESTNET_API_KEY and BINANCE_TESTNET_API_SECRET; never use production keys."
            )
        if client is not None:
            # Dependency injection is useful for tests, but must not permit an
            # alternate host to route signed requests to a production exchange.
            client_base_url = str(getattr(client, "base_url", "")).rstrip("/")
            if client_base_url != TESTNET_BASE_URL:
                raise BinanceTestnetConfigurationError(
                    "Injected client must use the fixed Binance Futures TESTNET base URL."
                )
            self._client = client
        else:
            self._client = httpx.Client(
                base_url=TESTNET_BASE_URL,
                headers={"X-MBX-APIKEY": self.api_key},
                timeout=10.0,
            )

    def _signed_params(self, params):
        payload = dict(params)
        payload["timestamp"] = int(time.time() * 1000)
        payload.setdefault("recvWindow", 5000)
        query = urlencode(payload)
        payload["signature"] = hmac.new(
            self.api_secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return payload

    def _assert_testnet_base_url(self):
        if str(getattr(self._client, "base_url", "")).rstrip("/") != TESTNET_BASE_URL:
            raise BinanceTestnetConfigurationError(
                "Refusing request: client base URL is not the fixed Binance Futures TESTNET host."
            )

    def _get(self, path, *, params):
        self._assert_testnet_base_url()
        return self._client.get(path, params=params)

    def _post(self, path, *, params):
        self._assert_testnet_base_url()
        return self._client.post(path, params=params)

    def _delete(self, path, *, params):
        self._assert_testnet_base_url()
        return self._client.delete(path, params=params)

    def account_risk_metrics(self, symbol):
        """Return gross account exposure metrics from signed TESTNET account state.

        Fails closed if exchange notional is missing for an open position.
        """
        if not isinstance(symbol, str) or not symbol.isascii() or not symbol.endswith("USDT") or not symbol[:-4].isalnum():
            return {"ok": False, "reason": "invalid_symbol"}
        response = self._get("/fapi/v2/account", params=self._signed_params({}))
        response.raise_for_status()
        account = response.json()
        if not isinstance(account, dict) or not isinstance(account.get("positions"), list):
            return {"ok": False, "reason": "invalid_testnet_account_response"}
        open_positions = 0
        total_notional = Decimal("0")
        symbol_notional = Decimal("0")
        try:
            for position in account["positions"]:
                if not isinstance(position, dict):
                    return {"ok": False, "reason": "invalid_position_record"}
                amount = Decimal(str(position.get("positionAmt", "0")))
                if not amount.is_finite():
                    return {"ok": False, "reason": "invalid_position_amount"}
                if amount == 0:
                    continue
                open_positions += 1
                raw_notional = position.get("notional")
                if raw_notional in (None, ""):
                    return {"ok": False, "reason": "position_notional_missing"}
                notional = abs(Decimal(str(raw_notional)))
                if not notional.is_finite():
                    return {"ok": False, "reason": "position_notional_invalid"}
                total_notional += notional
                if position.get("symbol") == symbol:
                    symbol_notional += notional
        except (InvalidOperation, TypeError, ValueError):
            return {"ok": False, "reason": "invalid_position_risk_values"}
        return {
            "ok": True, "mode": "testnet_account_risk_metrics",
            "open_positions": open_positions,
            "symbol_notional_usdt": float(symbol_notional),
            "total_notional_usdt": float(total_notional),
        }

    def symbol_rules(self, symbol):
        """Read testnet exchange filters; does not submit or create an order."""
        if not isinstance(symbol, str) or not symbol.isascii() or not symbol.endswith("USDT") or not symbol[:-4].isalnum():
            raise ValueError("invalid_symbol")
        response = self._get("/fapi/v1/exchangeInfo", params={"symbol": symbol})
        response.raise_for_status()
        payload = response.json()
        matches = [item for item in payload.get("symbols", []) if item.get("symbol") == symbol]
        if len(matches) != 1:
            raise ValueError("symbol_not_found_or_ambiguous")
        filters = {item.get("filterType"): item for item in matches[0].get("filters", [])}
        lot = filters.get("MARKET_LOT_SIZE") or filters.get("LOT_SIZE")
        if not lot:
            raise ValueError("market_lot_size_filter_missing")
        min_notional_filter = filters.get("MIN_NOTIONAL") or filters.get("NOTIONAL")
        return {
            "symbol": symbol,
            "status": matches[0].get("status"),
            "quantity_step": lot.get("stepSize"),
            "min_quantity": lot.get("minQty"),
            "max_quantity": lot.get("maxQty"),
            "min_notional": (
                min_notional_filter.get("notional")
                or min_notional_filter.get("minNotional")
                if min_notional_filter else None
            ),
        }

    def account_preflight(self):
        """Verify signed testnet account access without placing or modifying orders."""
        response = self._get(
            "/fapi/v2/account",
            params=self._signed_params({}),
        )
        response.raise_for_status()
        account = response.json()
        if (
            not isinstance(account, dict)
            or not isinstance(account.get("canTrade"), bool)
            or not isinstance(account.get("positions"), list)
            or any(not isinstance(position, dict) for position in account["positions"])
        ):
            raise ValueError("invalid_testnet_account_response")
        open_position_count = 0
        for position in account["positions"]:
            try:
                amount = Decimal(str(position.get("positionAmt", "0")))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise ValueError("invalid_testnet_account_position") from exc
            if not amount.is_finite():
                raise ValueError("invalid_testnet_account_position")
            if amount != 0:
                open_position_count += 1
        return {
            "connected": True,
            "mode": "testnet",
            "can_trade": account["canTrade"],
            "open_position_count": open_position_count,
        }

    def validate_order(self, intent: OrderIntent, config=None):
        """Submit an order to Binance's TESTNET validation-only endpoint."""
        if config is None:
            config = ExecutionConfig(mode="testnet")
        decision = validate_order_intent(intent, config)
        if not decision.allowed:
            return {"accepted": False, "reason": decision.reason}
        if config.mode != "testnet":
            return {"accepted": False, "reason": "testnet_mode_required"}
        try:
            rules = self.symbol_rules(intent.symbol)
            if rules["status"] != "TRADING":
                return {"accepted": False, "reason": "symbol_not_trading"}
            quantity = Decimal(str(intent.quantity))
            step = Decimal(str(rules["quantity_step"]))
            minimum = Decimal(str(rules["min_quantity"]))
            maximum = Decimal(str(rules["max_quantity"]))
            if step <= 0 or minimum <= 0 or maximum < minimum:
                return {"accepted": False, "reason": "invalid_exchange_quantity_filters"}
            quantity = (quantity / step).to_integral_value(rounding=ROUND_DOWN) * step
            if quantity < minimum:
                return {"accepted": False, "reason": "quantity_below_exchange_minimum"}
            if quantity > maximum:
                return {"accepted": False, "reason": "quantity_above_exchange_maximum"}
            min_notional = rules["min_notional"]
            if min_notional is not None and Decimal(str(intent.entry)) * quantity < Decimal(str(min_notional)):
                return {"accepted": False, "reason": "notional_below_exchange_minimum"}
            quantity_text = format(quantity, "f")
            if "." in quantity_text:
                quantity_text = quantity_text.rstrip("0").rstrip(".")
            params = {
                "symbol": intent.symbol,
                "side": intent.side,
                "type": "MARKET",
                "quantity": quantity_text,
            }
        except (ValueError, InvalidOperation, TypeError, KeyError):
            return {"accepted": False, "reason": "exchange_preflight_failed"}
        response = self._post("/fapi/v1/order/test", params=self._signed_params(params))
        response.raise_for_status()
        return {
            "accepted": True,
            "mode": "testnet_validation_only",
            "symbol": intent.symbol,
            "side": intent.side,
            "quantity": params["quantity"],
            "notional_usdt_estimate": round(decision.notional_usdt, 8),
            "risk_usdt_estimate": round(decision.risk_usdt, 8),
        }

    def submit_testnet_market_order(self, intent: OrderIntent, client_order_id: str, config=None):
        """Submit a MARKET order to TESTNET only; production trading is not supported.

        This is deliberately a low-level testnet integration step, not a complete
        trading lifecycle: callers must not use it until position reconciliation,
        protective exits, kill-switch enforcement and restart recovery are integrated.
        """
        if config is None:
            config = ExecutionConfig(mode="testnet")
        if not isinstance(config, ExecutionConfig):
            return {"accepted": False, "reason": "invalid_execution_config"}
        if config.mode != "testnet" or config.live_trading_enabled:
            return {"accepted": False, "reason": "testnet_mode_required"}
        if not isinstance(client_order_id, str) or not client_order_id or len(client_order_id) > 36:
            return {"accepted": False, "reason": "invalid_client_order_id"}
        if not client_order_id.isascii() or not client_order_id.replace("-", "").replace("_", "").isalnum():
            return {"accepted": False, "reason": "invalid_client_order_id"}
        decision = validate_order_intent(intent, config)
        if not decision.allowed:
            return {"accepted": False, "reason": decision.reason}
        try:
            rules = self.symbol_rules(intent.symbol)
            if rules["status"] != "TRADING":
                return {"accepted": False, "reason": "symbol_not_trading"}
            quantity = Decimal(str(intent.quantity))
            step = Decimal(str(rules["quantity_step"]))
            minimum = Decimal(str(rules["min_quantity"]))
            maximum = Decimal(str(rules["max_quantity"]))
            if step <= 0 or minimum <= 0 or maximum < minimum:
                return {"accepted": False, "reason": "invalid_exchange_quantity_filters"}
            quantity = (quantity / step).to_integral_value(rounding=ROUND_DOWN) * step
            if quantity < minimum:
                return {"accepted": False, "reason": "quantity_below_exchange_minimum"}
            if quantity > maximum:
                return {"accepted": False, "reason": "quantity_above_exchange_maximum"}
            min_notional = rules["min_notional"]
            if min_notional is not None and Decimal(str(intent.entry)) * quantity < Decimal(str(min_notional)):
                return {"accepted": False, "reason": "notional_below_exchange_minimum"}
            quantity_text = format(quantity, "f")
            if "." in quantity_text:
                quantity_text = quantity_text.rstrip("0").rstrip(".")
        except (ValueError, InvalidOperation, TypeError, KeyError):
            return {"accepted": False, "reason": "exchange_preflight_failed"}
        params = self._signed_params({
            "symbol": intent.symbol,
            "side": intent.side,
            "type": "MARKET",
            "quantity": quantity_text,
            "newClientOrderId": client_order_id,
        })
        response = self._post("/fapi/v1/order", params=params)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or "orderId" not in payload:
            return {"accepted": False, "reason": "unexpected_testnet_order_response"}
        return {
            "accepted": True,
            "mode": "testnet_order_submitted",
            "symbol": intent.symbol,
            "side": intent.side,
            "quantity": quantity_text,
            "client_order_id": client_order_id,
            "exchange_order_id": str(payload["orderId"]),
            "status": payload.get("status", "UNKNOWN"),
        }

    def query_testnet_order(self, symbol: str, client_order_id: str):
        """Reconcile an existing TESTNET order by its client ID; never retries or submits."""
        if not isinstance(symbol, str) or not symbol.isascii() or not symbol.endswith("USDT") or not symbol[:-4].isalnum():
            return {"found": False, "reason": "invalid_symbol"}
        if not isinstance(client_order_id, str) or not client_order_id or len(client_order_id) > 36:
            return {"found": False, "reason": "invalid_client_order_id"}
        if not client_order_id.isascii() or not client_order_id.replace("-", "").replace("_", "").isalnum():
            return {"found": False, "reason": "invalid_client_order_id"}
        response = self._get(
            "/fapi/v1/order",
            params=self._signed_params({
                "symbol": symbol,
                "origClientOrderId": client_order_id,
            }),
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or "orderId" not in payload:
            return {"found": False, "reason": "unexpected_testnet_order_response"}
        return {
            "found": True,
            "mode": "testnet_order_reconciliation",
            "symbol": payload.get("symbol", symbol),
            "client_order_id": payload.get("clientOrderId", client_order_id),
            "exchange_order_id": str(payload["orderId"]),
            "status": payload.get("status", "UNKNOWN"),
            "executed_quantity": payload.get("executedQty", "0"),
            "average_price": payload.get("avgPrice", "0"),
        }

    def testnet_position_snapshot(self, symbol: str):
        """Read authoritative TESTNET position and open orders; no mutations."""
        if not isinstance(symbol, str) or not symbol.isascii() or not symbol.endswith("USDT") or not symbol[:-4].isalnum():
            return {"ok": False, "reason": "invalid_symbol"}
        account_response = self._get(
            "/fapi/v2/account",
            params=self._signed_params({}),
        )
        account_response.raise_for_status()
        account = account_response.json()
        if not isinstance(account, dict) or not isinstance(account.get("positions"), list):
            return {"ok": False, "reason": "invalid_testnet_account_response"}
        if any(not isinstance(position, dict) for position in account["positions"]):
            return {"ok": False, "reason": "invalid_testnet_position_records"}
        matching = [p for p in account["positions"] if p.get("symbol") == symbol]
        if len(matching) != 1:
            return {"ok": False, "reason": "position_not_found_or_ambiguous"}
        position = matching[0]
        try:
            amount = Decimal(str(position.get("positionAmt", "0")))
            entry_price = Decimal(str(position.get("entryPrice", "0")))
            if not amount.is_finite() or not entry_price.is_finite() or entry_price < 0:
                raise InvalidOperation
        except (InvalidOperation, TypeError, ValueError):
            return {"ok": False, "reason": "invalid_testnet_position_values"}
        orders_response = self._get(
            "/fapi/v1/openOrders",
            params=self._signed_params({"symbol": symbol}),
        )
        orders_response.raise_for_status()
        orders = orders_response.json()
        if not isinstance(orders, list) or any(not isinstance(order, dict) for order in orders):
            return {"ok": False, "reason": "invalid_testnet_open_orders_response"}
        required_order_fields = ("orderId", "side", "type", "status", "reduceOnly")
        for order in orders:
            if any(field not in order for field in required_order_fields):
                return {"ok": False, "reason": "invalid_testnet_open_order_record"}
            if not isinstance(order["reduceOnly"], bool):
                return {"ok": False, "reason": "invalid_testnet_open_order_reduce_only"}
            if not all(isinstance(order[field], str) and order[field] for field in ("side", "type", "status")):
                return {"ok": False, "reason": "invalid_testnet_open_order_record"}
        return {
            "ok": True,
            "mode": "testnet_read_only_snapshot",
            "symbol": symbol,
            "position_amount": str(amount),
            "entry_price": str(entry_price),
            "open_orders": [
                {
                    "order_id": str(order.get("orderId", "")),
                    "client_order_id": order.get("clientOrderId", ""),
                    "side": order.get("side", ""),
                    "type": order.get("type", ""),
                    "status": order.get("status", ""),
                    "reduce_only": bool(order.get("reduceOnly", False)),
                    "stop_price": order.get("stopPrice", "0"),
                    "orig_quantity": order.get("origQty", "0"),
                    "executed_quantity": order.get("executedQty", "0"),
                }
                for order in orders
            ],
        }

    def submit_testnet_protective_exit(
        self,
        symbol: str,
        exit_kind: str,
        quantity,
        trigger_price,
        client_order_id: str,
    ):
        """Place one reduce-only conditional exit on TESTNET for an existing position.

        This deliberately does not retry ambiguous requests. Query by client order ID
        and inspect the position/open-order snapshot before taking recovery action.
        """
        if not isinstance(symbol, str) or not symbol.isascii() or not symbol.endswith("USDT") or not symbol[:-4].isalnum():
            return {"accepted": False, "reason": "invalid_symbol"}
        if not isinstance(exit_kind, str) or exit_kind not in {"stop_loss", "take_profit"}:
            return {"accepted": False, "reason": "invalid_exit_kind"}
        if not isinstance(client_order_id, str) or not client_order_id or len(client_order_id) > 36:
            return {"accepted": False, "reason": "invalid_client_order_id"}
        if not client_order_id.isascii() or not client_order_id.replace("-", "").replace("_", "").isalnum():
            return {"accepted": False, "reason": "invalid_client_order_id"}
        try:
            requested_quantity = Decimal(str(quantity))
            trigger = Decimal(str(trigger_price))
            if not requested_quantity.is_finite() or requested_quantity <= 0:
                raise InvalidOperation
            if not trigger.is_finite() or trigger <= 0:
                raise InvalidOperation
        except (InvalidOperation, TypeError, ValueError):
            return {"accepted": False, "reason": "invalid_quantity_or_trigger"}
        snapshot = self.testnet_position_snapshot(symbol)
        if not snapshot.get("ok"):
            return {"accepted": False, "reason": "position_snapshot_failed"}
        try:
            position_amount = Decimal(snapshot["position_amount"])
            entry_price = Decimal(snapshot["entry_price"])
        except (InvalidOperation, TypeError, ValueError, KeyError):
            return {"accepted": False, "reason": "invalid_position_snapshot"}
        if position_amount == 0 or entry_price <= 0:
            return {"accepted": False, "reason": "no_open_position"}
        is_long = position_amount > 0
        if exit_kind == "stop_loss":
            geometry_ok = trigger < entry_price if is_long else trigger > entry_price
            order_type = "STOP_MARKET"
        else:
            geometry_ok = trigger > entry_price if is_long else trigger < entry_price
            order_type = "TAKE_PROFIT_MARKET"
        if not geometry_ok:
            return {"accepted": False, "reason": "invalid_protective_trigger_geometry"}
        if requested_quantity > abs(position_amount):
            return {"accepted": False, "reason": "exit_quantity_exceeds_position"}
        try:
            rules = self.symbol_rules(symbol)
            if rules["status"] != "TRADING":
                return {"accepted": False, "reason": "symbol_not_trading"}
            step = Decimal(str(rules["quantity_step"]))
            minimum = Decimal(str(rules["min_quantity"]))
            maximum = Decimal(str(rules["max_quantity"]))
            if step <= 0 or minimum <= 0 or maximum < minimum:
                return {"accepted": False, "reason": "invalid_exchange_quantity_filters"}
            rounded = (requested_quantity / step).to_integral_value(rounding=ROUND_DOWN) * step
            if rounded < minimum:
                return {"accepted": False, "reason": "quantity_below_exchange_minimum"}
            if rounded > maximum or rounded > abs(position_amount):
                return {"accepted": False, "reason": "exit_quantity_exceeds_position_or_exchange_maximum"}
            quantity_text = format(rounded, "f")
            if "." in quantity_text:
                quantity_text = quantity_text.rstrip("0").rstrip(".")
            trigger_text = format(trigger, "f")
            params = self._signed_params({
                "symbol": symbol,
                "side": "SELL" if is_long else "BUY",
                "type": order_type,
                "quantity": quantity_text,
                "stopPrice": trigger_text,
                "reduceOnly": "true",
                "workingType": "MARK_PRICE",
                "newClientOrderId": client_order_id,
            })
        except (ValueError, InvalidOperation, TypeError, KeyError):
            return {"accepted": False, "reason": "exchange_preflight_failed"}
        response = self._post("/fapi/v1/order", params=params)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or "orderId" not in payload:
            return {"accepted": False, "reason": "unexpected_testnet_order_response"}
        return {
            "accepted": True,
            "mode": "testnet_protective_exit_submitted",
            "symbol": symbol,
            "exit_kind": exit_kind,
            "side": params["side"],
            "type": order_type,
            "quantity": quantity_text,
            "trigger_price": trigger_text,
            "reduce_only": True,
            "client_order_id": client_order_id,
            "exchange_order_id": str(payload["orderId"]),
            "status": payload.get("status", "UNKNOWN"),
        }

    def cancel_testnet_order(self, symbol: str, client_order_id: str):
        """Cancel one known TESTNET order by client ID; never cancels by broad symbol."""
        if not isinstance(symbol, str) or not symbol.isascii() or not symbol.endswith("USDT") or not symbol[:-4].isalnum():
            return {"cancelled": False, "reason": "invalid_symbol"}
        if not isinstance(client_order_id, str) or not client_order_id or len(client_order_id) > 36:
            return {"cancelled": False, "reason": "invalid_client_order_id"}
        if not client_order_id.isascii() or not client_order_id.replace("-", "").replace("_", "").isalnum():
            return {"cancelled": False, "reason": "invalid_client_order_id"}
        response = self._delete(
            "/fapi/v1/order",
            params=self._signed_params({
                "symbol": symbol,
                "origClientOrderId": client_order_id,
            }),
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or "orderId" not in payload:
            return {"cancelled": False, "reason": "unexpected_testnet_cancel_response"}
        return {
            "cancelled": payload.get("status") == "CANCELED",
            "mode": "testnet_order_cancelled",
            "symbol": payload.get("symbol", symbol),
            "client_order_id": payload.get("clientOrderId", client_order_id),
            "exchange_order_id": str(payload["orderId"]),
            "status": payload.get("status", "UNKNOWN"),
        }

    def close(self):
        self._client.close()
