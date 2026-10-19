"""Read-only production-account readiness checks. This module NEVER places orders.

It is intentionally the next step when TESTNET is skipped: validate production
credentials, account state, symbol rules and explicit operator acknowledgements
without risking an order. It is not a live execution adapter.
"""
import hashlib
import hmac
import os
import time
from urllib.parse import urlencode

import httpx

BINANCE_LIVE_BASE_URL = "https://fapi.binance.com"


class LiveReadinessConfigurationError(RuntimeError):
    pass


class BinanceLiveReadiness:
    """Read-only USD-M futures production preflight; no trading endpoints exposed."""

    def __init__(self, api_key=None, api_secret=None, client=None):
        self.api_key = api_key if api_key is not None else os.getenv("BINANCE_LIVE_API_KEY", "")
        self.api_secret = api_secret if api_secret is not None else os.getenv("BINANCE_LIVE_API_SECRET", "")
        if not self.api_key or not self.api_secret:
            raise LiveReadinessConfigurationError(
                "Set BINANCE_LIVE_API_KEY and BINANCE_LIVE_API_SECRET in the deployment secret store."
            )
        self._client = client or httpx.Client(
            base_url=BINANCE_LIVE_BASE_URL,
            headers={"X-MBX-APIKEY": self.api_key},
            timeout=10.0,
        )

    def _signed_get(self, path, params=None):
        payload = dict(params or {})
        payload["timestamp"] = int(time.time() * 1000)
        payload["recvWindow"] = 5000
        query = urlencode(payload)
        payload["signature"] = hmac.new(
            self.api_secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        response = self._client.get(path, params=payload)
        response.raise_for_status()
        return response.json()

    def account_snapshot(self):
        """Read account state only; intentionally no POST/PUT/DELETE methods."""
        account = self._signed_get("/fapi/v2/account")
        positions = account.get("positions")
        if not isinstance(positions, list):
            raise ValueError("invalid_account_positions")
        open_positions = []
        gross_notional = 0.0
        for item in positions:
            try:
                amount = float(item["positionAmt"])
                mark = float(item.get("markPrice", 0) or 0)
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                raise ValueError("invalid_account_position") from exc
            if amount != 0:
                if mark <= 0:
                    # Account endpoint can omit markPrice. Fetch positionRisk below.
                    continue
                gross_notional += abs(amount * mark)
                open_positions.append({
                    "symbol": item.get("symbol"),
                    "position_amount": amount,
                    "entry_price": float(item.get("entryPrice", 0) or 0),
                    "notional_usdt": abs(amount * mark),
                })
        position_risk = self._signed_get("/fapi/v2/positionRisk")
        if not isinstance(position_risk, list):
            raise ValueError("invalid_position_risk")
        open_positions = []
        gross_notional = 0.0
        for item in position_risk:
            try:
                amount = float(item["positionAmt"])
                mark = float(item["markPrice"])
                entry = float(item["entryPrice"])
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                raise ValueError("invalid_position_risk_item") from exc
            if amount != 0:
                if mark <= 0:
                    raise ValueError("invalid_mark_price_for_open_position")
                notional = abs(amount * mark)
                gross_notional += notional
                open_positions.append({
                    "symbol": item.get("symbol"),
                    "position_amount": amount,
                    "entry_price": entry,
                    "mark_price": mark,
                    "notional_usdt": notional,
                    "unrealized_pnl_usdt": float(item.get("unRealizedProfit", 0) or 0),
                })
        try:
            available_balance = float(account["availableBalance"])
            total_wallet_balance = float(account["totalWalletBalance"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError("invalid_account_balances") from exc
        if available_balance < 0 or total_wallet_balance < 0:
            raise ValueError("negative_account_balance")
        return {
            "read_only": True,
            "order_submission_supported": False,
            "available_balance_usdt": available_balance,
            "total_wallet_balance_usdt": total_wallet_balance,
            "open_positions": open_positions,
            "open_position_count": len(open_positions),
            "gross_open_notional_usdt": gross_notional,
            "account_permissions": account.get("canTrade"),
            "multi_assets_mode": account.get("multiAssetsMargin"),
            "position_mode": self._signed_get("/fapi/v1/positionSide/dual"),
        }

    def symbol_rules(self, symbol):
        """Public market metadata only; no order endpoint is available here."""
        if not isinstance(symbol, str) or not symbol.endswith("USDT") or not symbol[:-4].isalnum():
            raise ValueError("invalid_symbol")
        response = self._client.get("/fapi/v1/exchangeInfo", params={"symbol": symbol})
        response.raise_for_status()
        payload = response.json()
        symbols = payload.get("symbols")
        if not isinstance(symbols, list):
            raise ValueError("invalid_exchange_info")
        for item in symbols:
            if item.get("symbol") == symbol:
                return {
                    "symbol": symbol,
                    "status": item.get("status"),
                    "contract_type": item.get("contractType"),
                    "quote_asset": item.get("quoteAsset"),
                    "filters": item.get("filters", []),
                }
        raise ValueError("symbol_not_found")
