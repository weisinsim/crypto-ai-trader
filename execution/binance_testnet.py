"""Binance USD-M futures TESTNET adapter skeleton.

This adapter only targets the Binance futures testnet host. It does not contain
or select a production endpoint. API credentials must be supplied through
environment variables and must never be committed to the repository.
"""
import hashlib
import hmac
import os
import time
from urllib.parse import urlencode

import httpx

from execution.safety import ExecutionConfig, OrderIntent, validate_order_intent

TESTNET_BASE_URL = "https://testnet.binancefuture.com"


class TestnetConfigurationError(RuntimeError):
    pass


class BinanceFuturesTestnet:
    def __init__(self, api_key=None, api_secret=None, client=None):
        self.api_key = api_key if api_key is not None else os.getenv("BINANCE_TESTNET_API_KEY", "")
        self.api_secret = api_secret if api_secret is not None else os.getenv("BINANCE_TESTNET_API_SECRET", "")
        if not self.api_key or not self.api_secret:
            raise TestnetConfigurationError(
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

    def validate_order(self, intent: OrderIntent, config=None):
        """Submit an order to Binance's TESTNET validation-only endpoint."""
        config = config or ExecutionConfig(mode="testnet")
        decision = validate_order_intent(intent, config)
        if not decision.allowed:
            return {"accepted": False, "reason": decision.reason}
        if config.mode != "testnet":
            return {"accepted": False, "reason": "testnet_mode_required"}
        params = {
            "symbol": intent.symbol,
            "side": intent.side,
            "type": "MARKET",
            "quantity": format(intent.quantity, ".8f").rstrip("0").rstrip("."),
        }
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
