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

    def account_risk_metrics(self, symbol):
        """Return gross account exposure metrics from signed TESTNET account state.

        Fails closed if exchange notional is missing for an open position.
        """
        if not isinstance(symbol, str) or not symbol.endswith("USDT") or not symbol[:-4].isalnum():
            return {"ok": False, "reason": "invalid_symbol"}
        response = self._client.get("/fapi/v2/account", params=self._signed_params({}))
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
                if "positionAmt" not in position:
                    return {"ok": False, "reason": "invalid_position_amount"}
                amount = Decimal(str(position["positionAmt"]))
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
        try:
            symbol_notional_float = float(symbol_notional)
            total_notional_float = float(total_notional)
        except (OverflowError, ValueError):
            return {"ok": False, "reason": "position_notional_out_of_range"}
        from math import isfinite
        if not isfinite(symbol_notional_float) or not isfinite(total_notional_float):
            return {"ok": False, "reason": "position_notional_out_of_range"}
        return {
            "ok": True, "mode": "testnet_account_risk_metrics",
            "open_positions": open_positions,
            "symbol_notional_usdt": symbol_notional_float,
            "total_notional_usdt": total_notional_float,
        }

    def symbol_rules(self, symbol):
        """Read testnet exchange filters; does not submit or create an order."""
        if not symbol.endswith("USDT") or not symbol[:-4].isalnum():
            raise ValueError("invalid_symbol")
        response = self._client.get("/fapi/v1/exchangeInfo", params={"symbol": symbol})
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("symbols"), list):
            raise ValueError("invalid_exchange_info_response")
        if any(not isinstance(item, dict) for item in payload["symbols"]):
            raise ValueError("invalid_exchange_info_symbol_record")
        matches = [item for item in payload["symbols"] if item.get("symbol") == symbol]
        if len(matches) != 1:
            raise ValueError("symbol_not_found_or_ambiguous")
        raw_filters = matches[0].get("filters")
        if not isinstance(raw_filters, list) or any(not isinstance(item, dict) for item in raw_filters):
            raise ValueError("invalid_exchange_info_filters")
        filters = {item.get("filterType"): item for item in raw_filters if isinstance(item.get("filterType"), str)}
        lot = filters.get("MARKET_LOT_SIZE") or filters.get("LOT_SIZE")
        if not isinstance(lot, dict):
            raise ValueError("market_lot_size_filter_missing")
        if any(field not in lot for field in ("stepSize", "minQty", "maxQty")):
            raise ValueError("invalid_exchange_quantity_filters")
        min_notional_filter = filters.get("MIN_NOTIONAL") or filters.get("NOTIONAL")
        if min_notional_filter is not None and not any(
            min_notional_filter.get(field) not in (None, "") for field in ("notional", "minNotional")
        ):
            min_notional_filter = None
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
        response = self._client.get(
            "/fapi/v2/account",
            params=self._signed_params({}),
        )
        response.raise_for_status()
        account = response.json()
        if (
            not isinstance(account, dict)
            or not isinstance(account.get("canTrade"), bool)
            or not isinstance(account.get("positions"), list)
        ):
            raise ValueError("invalid_testnet_account_response")
        open_position_count = 0
        for position in account["positions"]:
            if not isinstance(position, dict):
                raise ValueError("invalid_testnet_position_record")
            if "positionAmt" not in position:
                raise ValueError("invalid_testnet_position_amount")
            try:
                amount = Decimal(str(position["positionAmt"]))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise ValueError("invalid_testnet_position_amount") from exc
            if not amount.is_finite():
                raise ValueError("invalid_testnet_position_amount")
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

    def submit_testnet_market_order(self, intent: OrderIntent, client_order_id: str, config=None):
        """Submit a MARKET order to TESTNET only; production trading is not supported.

        This is deliberately a low-level testnet integration step, not a complete
        trading lifecycle: callers must not use it until position reconciliation,
        protective exits, kill-switch enforcement and restart recovery are integrated.
        """
        config = config or ExecutionConfig(mode="testnet")
        if config.mode != "testnet" or config.live_trading_enabled:
            return {"accepted": False, "reason": "testnet_mode_required"}
        if not isinstance(client_order_id, str) or not client_order_id or len(client_order_id) > 36:
            return {"accepted": False, "reason": "invalid_client_order_id"}
        if not client_order_id.replace("-", "").replace("_", "").isalnum():
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
        try:
            response = self._client.post("/fapi/v1/order", params=params)
            response.raise_for_status()
            payload = response.json()
        except Exception:
            # The exchange may have accepted the order before the transport failed.
            # Never signal a retry-safe failure; reconcile using this client order ID.
            return {
                "accepted": False,
                "reason": "order_submission_outcome_unknown",
                "mode": "testnet_reconciliation_required",
                "symbol": intent.symbol,
                "client_order_id": client_order_id,
            }
        if not isinstance(payload, dict) or "orderId" not in payload:
            return {
                "accepted": False,
                "reason": "order_submission_outcome_unknown",
                "mode": "testnet_reconciliation_required",
                "symbol": intent.symbol,
                "client_order_id": client_order_id,
            }
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
        if not symbol.endswith("USDT") or not symbol[:-4].isalnum():
            return {"found": False, "reason": "invalid_symbol"}
        if not isinstance(client_order_id, str) or not client_order_id or len(client_order_id) > 36:
            return {"found": False, "reason": "invalid_client_order_id"}
        if not client_order_id.replace("-", "").replace("_", "").isalnum():
            return {"found": False, "reason": "invalid_client_order_id"}
        response = self._client.get(
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
        returned_symbol = payload.get("symbol")
        returned_client_id = payload.get("clientOrderId")
        status = payload.get("status")
        if returned_symbol != symbol or returned_client_id != client_order_id:
            return {"found": False, "reason": "testnet_order_identity_mismatch"}
        if not isinstance(status, str) or not status:
            return {"found": False, "reason": "invalid_testnet_order_status"}
        try:
            executed_quantity = Decimal(str(payload["executedQty"]))
            average_price = Decimal(str(payload.get("avgPrice", "0")))
            if (
                not executed_quantity.is_finite()
                or executed_quantity < 0
                or not average_price.is_finite()
                or average_price < 0
            ):
                raise InvalidOperation
        except (KeyError, InvalidOperation, TypeError, ValueError):
            return {"found": False, "reason": "invalid_testnet_order_fill_values"}
        return {
            "found": True,
            "mode": "testnet_order_reconciliation",
            "symbol": returned_symbol,
            "client_order_id": returned_client_id,
            "exchange_order_id": str(payload["orderId"]),
            "status": status,
            "executed_quantity": str(executed_quantity),
            "average_price": str(average_price),
        }

    def testnet_position_snapshot(self, symbol: str):
        """Read authoritative TESTNET position and open orders; no mutations."""
        if not isinstance(symbol, str) or not symbol.endswith("USDT") or not symbol[:-4].isalnum():
            return {"ok": False, "reason": "invalid_symbol"}
        account_response = self._client.get(
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
        if "positionAmt" not in position:
            return {"ok": False, "reason": "invalid_testnet_position_values"}
        try:
            amount = Decimal(str(position["positionAmt"]))
            if amount != 0 and "entryPrice" not in position:
                raise InvalidOperation
            entry_price = Decimal(str(position.get("entryPrice", "0")))
            if not amount.is_finite() or not entry_price.is_finite() or entry_price < 0:
                raise InvalidOperation
        except (InvalidOperation, TypeError, ValueError):
            return {"ok": False, "reason": "invalid_testnet_position_values"}
        orders_response = self._client.get(
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
        if not isinstance(symbol, str) or not symbol.endswith("USDT") or not symbol[:-4].isalnum():
            return {"accepted": False, "reason": "invalid_symbol"}
        if exit_kind not in {"stop_loss", "take_profit"}:
            return {"accepted": False, "reason": "invalid_exit_kind"}
        if not isinstance(client_order_id, str) or not client_order_id or len(client_order_id) > 36:
            return {"accepted": False, "reason": "invalid_client_order_id"}
        if not client_order_id.replace("-", "").replace("_", "").isalnum():
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
        try:
            response = self._client.post("/fapi/v1/order", params=params)
            response.raise_for_status()
            payload = response.json()
        except Exception:
            # A transport/API decoding error can occur after the exchange accepted it.
            return {
                "accepted": False,
                "reason": "order_submission_outcome_unknown",
                "mode": "testnet_reconciliation_required",
                "symbol": symbol,
                "client_order_id": client_order_id,
            }
        if not isinstance(payload, dict) or "orderId" not in payload:
            return {
                "accepted": False,
                "reason": "order_submission_outcome_unknown",
                "mode": "testnet_reconciliation_required",
                "symbol": symbol,
                "client_order_id": client_order_id,
            }
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
        if not isinstance(symbol, str) or not symbol.endswith("USDT") or not symbol[:-4].isalnum():
            return {"cancelled": False, "reason": "invalid_symbol"}
        if not isinstance(client_order_id, str) or not client_order_id or len(client_order_id) > 36:
            return {"cancelled": False, "reason": "invalid_client_order_id"}
        if not client_order_id.replace("-", "").replace("_", "").isalnum():
            return {"cancelled": False, "reason": "invalid_client_order_id"}
        try:
            response = self._client.delete(
                "/fapi/v1/order",
                params=self._signed_params({
                    "symbol": symbol,
                    "origClientOrderId": client_order_id,
                }),
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            # Cancellation may have succeeded even if the response was lost.
            return {
                "cancelled": False,
                "reason": "order_cancellation_outcome_unknown",
                "mode": "testnet_reconciliation_required",
                "symbol": symbol,
                "client_order_id": client_order_id,
            }
        if not isinstance(payload, dict) or "orderId" not in payload:
            return {
                "cancelled": False,
                "reason": "order_cancellation_outcome_unknown",
                "mode": "testnet_reconciliation_required",
                "symbol": symbol,
                "client_order_id": client_order_id,
            }
        if payload.get("symbol") != symbol or payload.get("clientOrderId") != client_order_id:
            return {"cancelled": False, "reason": "testnet_order_identity_mismatch"}
        status = payload.get("status")
        if not isinstance(status, str) or not status:
            return {"cancelled": False, "reason": "invalid_testnet_cancel_status"}
        return {
            "cancelled": status == "CANCELED",
            "mode": "testnet_order_cancel_result",
            "symbol": symbol,
            "client_order_id": client_order_id,
            "exchange_order_id": str(payload["orderId"]),
            "status": status,
        }

    def close(self):
        self._client.close()
