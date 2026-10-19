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
        self._client = client or httpx.Client(
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

    def symbol_rules(self, symbol):
        """Read testnet exchange filters; does not submit or create an order."""
        if not symbol.endswith("USDT") or not symbol[:-4].isalnum():
            raise ValueError("invalid_symbol")
        response = self._client.get("/fapi/v1/exchangeInfo", params={"symbol": symbol})
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

    def validate_order(self, intent: OrderIntent, config=None):
        """Submit an order to Binance's TESTNET validation-only endpoint."""
        config = config or ExecutionConfig(mode="testnet")
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
        response = self._client.post("/fapi/v1/order/test", params=self._signed_params(params))
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

    def close(self):
        self._client.close()
