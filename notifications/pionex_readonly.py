"""Pionex futures read-only transport. No order placement or cancellation API exists here.

This adapter deliberately returns raw exchange JSON: position/fill reconciliation must
validate exchange schemas and pagination before any REAL_EXIT alert is emitted.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from urllib.parse import urlencode

import httpx


class PionexReadError(RuntimeError):
    pass


class PionexFuturesReader:
    BASE_URL = "https://api.pionex.com"
    ALLOWED_PATHS = frozenset({
        "/uapi/v1/account/positions",
        "/uapi/v1/account/historyPositions",
        "/uapi/v1/trade/fills",
        "/uapi/v1/trade/fillsByOrderId",
        "/uapi/v1/trade/historyOrders",
        "/uapi/v1/trade/openOrders",
        "/uapi/v1/trade/order",
    })

    def __init__(self, key: str, secret: str, *, client=None, clock_ms=None):
        if not isinstance(key, str) or not key.strip() or not isinstance(secret, str) or not secret.strip():
            raise ValueError("Pionex read-only credentials are required")
        self._key = key
        self._secret = secret
        self._client = client
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))

    @classmethod
    def from_env(cls, environ=None, **kwargs):
        env = os.environ if environ is None else environ
        if env.get("PIONEX_READ_ONLY_ENABLED", "").lower() != "true":
            raise PionexReadError("Pionex reader disabled by default")
        return cls(env.get("PIONEX_READ_API_KEY", ""), env.get("PIONEX_READ_API_SECRET", ""), **kwargs)

    async def get(self, path: str, **params):
        if path not in self.ALLOWED_PATHS:
            raise PionexReadError("Endpoint not on futures read-only allowlist")
        if any(k in {"timestamp", "signature"} for k in params):
            raise PionexReadError("Reserved query parameter")
        if any(v is None or isinstance(v, (dict, list, tuple, bool)) for v in params.values()):
            raise PionexReadError("Invalid query parameter")
        query = {k: str(v) for k, v in params.items()}
        query["timestamp"] = str(self._clock_ms())
        sorted_query = urlencode(sorted(query.items()), safe="_-", quote_via=__import__("urllib.parse", fromlist=["quote"]).quote)
        # Pionex signs unencoded sorted key=value pairs, while the URL is encoded.
        signing_query = "&".join(f"{k}={v}" for k, v in sorted(query.items()))
        signature = hmac.new(self._secret.encode(), f"GET{path}?{signing_query}".encode(), hashlib.sha256).hexdigest()
        headers = {"PIONEX-KEY": self._key, "PIONEX-SIGNATURE": signature}
        try:
            if self._client is None:
                async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
                    response = await client.get(self.BASE_URL + path + "?" + sorted_query, headers=headers)
            else:
                response = await self._client.get(self.BASE_URL + path + "?" + sorted_query, headers=headers)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise PionexReadError("Pionex read request failed; data not trustworthy") from None
        if not isinstance(payload, dict) or payload.get("result") is not True or not isinstance(payload.get("data"), (dict, list)):
            raise PionexReadError("Pionex response failed validation")
        return payload["data"]

    async def positions(self, symbol=None):
        return await self.get("/uapi/v1/account/positions", **({"symbol": symbol} if symbol else {}))

    async def fills(self, symbol, **filters):
        if not isinstance(symbol, str) or not symbol.endswith("_USDT_PERP"):
            raise PionexReadError("Explicit perpetual futures symbol required")
        if set(filters) - {"startTime", "endTime", "limit"}:
            raise PionexReadError("Unsupported fill filter")
        return await self.get("/uapi/v1/trade/fills", symbol=symbol, **filters)

    async def history_positions(self, symbol=None, **filters):
        if set(filters) - {"startTime", "endTime", "limit", "positionFlag"}:
            raise PionexReadError("Unsupported position filter")
        return await self.get("/uapi/v1/account/historyPositions", **({"symbol": symbol} if symbol else {}), **filters)
