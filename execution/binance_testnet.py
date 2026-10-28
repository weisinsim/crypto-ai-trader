"""Binance USD-M futures TESTNET adapter skeleton.

This adapter only targets the Binance futures testnet host. It does not contain
or select a production endpoint. API credentials must be supplied through
environment variables and must never be committed to the repository.
"""
import hashlib
from decimal import Decimal, InvalidOperation, ROUND_DOWN
import hmac
import math
import os
import time
from urllib.parse import urlencode

import httpx

from execution.safety import ExecutionConfig, OrderIntent, validate_order_intent
from execution.risk_ledger import RiskLedgerError, SQLiteRiskLedger

TESTNET_BASE_URL = "https://testnet.binancefuture.com"
MAX_PROTECTIVE_SNAPSHOT_DURATION_MS = 2000
MAX_PROTECTIVE_SNAPSHOT_AGE_MS = 2000


def _valid_exchange_order_id(value):
    return (
        isinstance(value, (str, int))
        and not isinstance(value, bool)
        and str(value).isdigit()
        and int(value) > 0
    )


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
        ledger_path = os.getenv("CRYPTO_TESTNET_RISK_LEDGER_PATH", "").strip()
        self._risk_ledger = None
        if ledger_path:
            try:
                self._risk_ledger = SQLiteRiskLedger(ledger_path)
            except RiskLedgerError:
                # Entry submission fails closed if persistent risk state is
                # configured but cannot be initialized.
                self._risk_ledger = None

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
        """Return gross exposure from signed TESTNET account state.

        Network/HTTP/JSON failures and unusable risk values fail closed. The
        caller must not treat an incomplete snapshot as zero exposure.
        """
        if not isinstance(symbol, str) or not symbol.isascii() or not symbol.endswith("USDT") or not symbol[:-4].isalnum():
            return {"ok": False, "reason": "invalid_symbol"}
        try:
            response = self._get("/fapi/v2/account", params=self._signed_params({}))
            response.raise_for_status()
            account = response.json()
        except Exception as exc:
            return {
                "ok": False,
                "reason": "account_risk_metrics_request_failed",
                "detail": {"exception_type": type(exc).__name__},
                "requires_reconciliation": True,
            }
        if (
            not isinstance(account, dict)
            or not isinstance(account.get("positions"), list)
            or account.get("canTrade") is not True
        ):
            return {
                "ok": False,
                "reason": "invalid_or_trading_disabled_testnet_account_response",
                "requires_reconciliation": True,
            }
        open_positions = 0
        total_notional = Decimal("0")
        symbol_notional = Decimal("0")
        seen_symbols = set()
        try:
            for position in account["positions"]:
                if (
                    not isinstance(position, dict)
                    or not isinstance(position.get("symbol"), str)
                    or not position.get("symbol")
                    or "positionAmt" not in position
                ):
                    # Missing position fields must never be interpreted as a
                    # flat position; that could understate account exposure.
                    return {"ok": False, "reason": "invalid_position_record", "requires_reconciliation": True}
                position_symbol = position["symbol"]
                if position_symbol in seen_symbols:
                    return {"ok": False, "reason": "duplicate_position_symbol", "requires_reconciliation": True}
                seen_symbols.add(position_symbol)
                amount = Decimal(str(position["positionAmt"]))
                if not amount.is_finite():
                    return {"ok": False, "reason": "invalid_position_amount", "requires_reconciliation": True}
                raw_notional = position.get("notional")
                if amount == 0:
                    # A flat amount paired with non-zero (or malformed)
                    # notional is internally inconsistent and cannot safely
                    # be counted as zero exposure.
                    if raw_notional not in (None, ""):
                        reported_notional = abs(Decimal(str(raw_notional)))
                        if not reported_notional.is_finite():
                            return {"ok": False, "reason": "position_notional_invalid", "requires_reconciliation": True}
                        if reported_notional > 0:
                            return {"ok": False, "reason": "flat_position_has_nonzero_notional", "requires_reconciliation": True}
                    continue
                open_positions += 1
                if raw_notional in (None, ""):
                    return {"ok": False, "reason": "position_notional_missing", "requires_reconciliation": True}
                notional = abs(Decimal(str(raw_notional)))
                if not notional.is_finite():
                    return {"ok": False, "reason": "position_notional_invalid", "requires_reconciliation": True}
                # A non-zero position with zero reported notional is not a
                # trustworthy exposure snapshot. Treating it as zero could let
                # a new entry pass the gross account cap incorrectly.
                if notional <= 0:
                    return {"ok": False, "reason": "position_notional_non_positive", "requires_reconciliation": True}
                total_notional += notional
                if position.get("symbol") == symbol:
                    symbol_notional += notional
            symbol_notional_float = float(symbol_notional)
            total_notional_float = float(total_notional)
            if not math.isfinite(symbol_notional_float) or not math.isfinite(total_notional_float):
                return {"ok": False, "reason": "risk_metrics_out_of_float_range", "requires_reconciliation": True}
        except (InvalidOperation, TypeError, ValueError, OverflowError):
            return {"ok": False, "reason": "invalid_position_risk_values", "requires_reconciliation": True}
        return {
            "ok": True, "mode": "testnet_account_risk_metrics",
            "open_positions": open_positions,
            "symbol_notional_usdt": symbol_notional_float,
            "total_notional_usdt": total_notional_float,
        }


    def open_entry_order_notional(self):
        """Return conservative notional reserved by outstanding entry orders.

        Open orders are a separate snapshot from account positions. Unknown or
        malformed orders fail closed; reduce-only/close-position exits are not
        counted as new exposure. This is a preflight guard, not an atomic
        reservation across exchange reads and order submission.
        """
        try:
            response = self._get(
                "/fapi/v1/openOrders", params=self._signed_params({})
            )
            response.raise_for_status()
            orders = response.json()
        except Exception as exc:
            return {
                "ok": False,
                "reason": "open_entry_orders_request_failed",
                "exception_type": type(exc).__name__,
                "requires_reconciliation": True,
            }
        if not isinstance(orders, list):
            return {
                "ok": False,
                "reason": "invalid_open_orders_response",
                "requires_reconciliation": True,
            }
        total_notional = Decimal("0")
        try:
            for order in orders:
                if not isinstance(order, dict):
                    return {"ok": False, "reason": "invalid_open_order_record", "requires_reconciliation": True}
                symbol = order.get("symbol")
                side = order.get("side")
                order_type = order.get("type")
                if (
                    not isinstance(symbol, str) or not symbol.isascii()
                    or not symbol.endswith("USDT") or not symbol[:-4].isalnum()
                    or side not in {"BUY", "SELL"}
                    or not isinstance(order_type, str) or not order_type
                ):
                    return {"ok": False, "reason": "invalid_open_order_identity", "requires_reconciliation": True}
                reduce_only = order.get("reduceOnly")
                close_position = order.get("closePosition")
                def parse_exchange_bool(value):
                    if isinstance(value, bool):
                        return value
                    if isinstance(value, str) and value.lower() in {"true", "false"}:
                        return value.lower() == "true"
                    return None

                reduce_only = parse_exchange_bool(order.get("reduceOnly"))
                close_position = parse_exchange_bool(order.get("closePosition"))
                if reduce_only is None or close_position is None:
                    # Unknown flags cannot safely be assumed to indicate an
                    # entry or an exit; refuse to undercount the account cap.
                    return {"ok": False, "reason": "open_order_exit_flags_invalid", "requires_reconciliation": True}
                if reduce_only or close_position:
                    continue
                if "origQty" not in order:
                    return {"ok": False, "reason": "open_entry_order_quantity_missing", "requires_reconciliation": True}
                quantity = Decimal(str(order["origQty"]))
                if not quantity.is_finite() or quantity <= 0:
                    return {"ok": False, "reason": "invalid_open_entry_order_quantity", "requires_reconciliation": True}
                raw_price = order.get("price")
                price = Decimal(str(raw_price)) if raw_price not in (None, "", "0", "0.0") else Decimal("0")
                if price <= 0:
                    raw_stop_price = order.get("stopPrice")
                    price = Decimal(str(raw_stop_price)) if raw_stop_price not in (None, "", "0", "0.0") else Decimal("0")
                if not price.is_finite() or price <= 0:
                    return {"ok": False, "reason": "open_entry_order_price_unknown", "requires_reconciliation": True}
                total_notional += quantity * price
            total_float = float(total_notional)
            if not math.isfinite(total_float):
                return {"ok": False, "reason": "open_entry_order_notional_out_of_range", "requires_reconciliation": True}
        except (InvalidOperation, TypeError, ValueError, OverflowError):
            return {"ok": False, "reason": "invalid_open_entry_order_risk_values", "requires_reconciliation": True}
        return {
            "ok": True,
            "mode": "testnet_open_entry_order_notional",
            "open_entry_order_notional_usdt": total_float,
            "open_entry_orders": len(orders),
        }

    def symbol_rules(self, symbol):
        """Read testnet exchange filters; does not submit or create an order."""
        if not isinstance(symbol, str) or not symbol.isascii() or not symbol.endswith("USDT") or not symbol[:-4].isalnum():
            raise ValueError("invalid_symbol")
        response = self._get("/fapi/v1/exchangeInfo", params={"symbol": symbol})
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("symbols"), list):
            raise ValueError("invalid_exchange_info_response")
        if any(not isinstance(item, dict) for item in payload["symbols"]):
            raise ValueError("invalid_exchange_symbol_records")
        matches = [item for item in payload["symbols"] if item.get("symbol") == symbol]
        if len(matches) != 1:
            raise ValueError("symbol_not_found_or_ambiguous")
        raw_filters = matches[0].get("filters")
        if not isinstance(raw_filters, list) or any(not isinstance(item, dict) for item in raw_filters):
            raise ValueError("invalid_exchange_filter_records")
        filters = {
            item.get("filterType"): item
            for item in raw_filters
            if isinstance(item.get("filterType"), str)
        }
        lot = filters.get("MARKET_LOT_SIZE") or filters.get("LOT_SIZE")
        if not lot:
            raise ValueError("market_lot_size_filter_missing")
        try:
            quantity_step = Decimal(str(lot["stepSize"]))
            min_quantity = Decimal(str(lot["minQty"]))
            max_quantity = Decimal(str(lot["maxQty"]))
        except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("invalid_exchange_quantity_filters") from exc
        if (
            not quantity_step.is_finite() or quantity_step <= 0
            or not min_quantity.is_finite() or min_quantity <= 0
            or not max_quantity.is_finite() or max_quantity < min_quantity
        ):
            raise ValueError("invalid_exchange_quantity_filters")
        price_filter = filters.get("PRICE_FILTER")
        price_tick_size = min_price = max_price = None
        if price_filter is not None:
            try:
                price_tick_size = Decimal(str(price_filter["tickSize"]))
                min_price = Decimal(str(price_filter["minPrice"]))
                max_price = Decimal(str(price_filter["maxPrice"]))
            except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
                raise ValueError("invalid_exchange_price_filters") from exc
            if (
                not price_tick_size.is_finite() or price_tick_size <= 0
                or not min_price.is_finite() or min_price < 0
                or not max_price.is_finite() or max_price <= min_price
            ):
                raise ValueError("invalid_exchange_price_filters")
        min_notional_filter = filters.get("MIN_NOTIONAL") or filters.get("NOTIONAL")
        min_notional = None
        if min_notional_filter is not None:
            raw_notional = min_notional_filter.get("notional") or min_notional_filter.get("minNotional")
            if raw_notional not in (None, ""):
                try:
                    parsed_notional = Decimal(str(raw_notional))
                except (InvalidOperation, TypeError, ValueError) as exc:
                    raise ValueError("invalid_exchange_min_notional") from exc
                if not parsed_notional.is_finite() or parsed_notional <= 0:
                    raise ValueError("invalid_exchange_min_notional")
                min_notional = str(parsed_notional)
        return {
            "symbol": symbol,
            "status": matches[0].get("status"),
            "quantity_step": str(quantity_step),
            "min_quantity": str(min_quantity),
            "max_quantity": str(max_quantity),
            "price_tick_size": str(price_tick_size) if price_tick_size is not None else None,
            "min_price": str(min_price) if min_price is not None else None,
            "max_price": str(max_price) if max_price is not None else None,
            "min_notional": min_notional,
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
        if self._risk_ledger is None:
            return {
                "accepted": False,
                "reason": "durable_risk_ledger_unavailable_requires_reconciliation",
                "requires_reconciliation": True,
                "client_order_id": client_order_id,
            }
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
        if self._risk_ledger is None:
            return {
                "accepted": False,
                "reason": "durable_risk_ledger_unavailable_requires_reconciliation",
                "requires_reconciliation": True,
                "client_order_id": client_order_id,
            }
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

            # A valid per-order decision is not sufficient if account exposure
            # is unknown or the new order would exceed the explicit gross cap.
            try:
                risk_metrics = self.account_risk_metrics(intent.symbol)
            except Exception as exc:
                return {
                    "accepted": False,
                    "reason": "account_risk_preflight_exception_requires_reconciliation",
                    "requires_reconciliation": True,
                    "exception_type": type(exc).__name__,
                }
            if (
                not isinstance(risk_metrics, dict)
                or risk_metrics.get("ok") is not True
                or risk_metrics.get("mode") != "testnet_account_risk_metrics"
            ):
                return {
                    "accepted": False,
                    "reason": "account_risk_preflight_failed_requires_reconciliation",
                    "requires_reconciliation": True,
                    "risk_metrics_reason": (
                        risk_metrics.get("reason") if isinstance(risk_metrics, dict)
                        else "invalid_risk_metrics_response"
                    ),
                }
            # A MARKET order can fill away from the signal's intended entry.
            # Use a fresh, identity-checked mark price for the exposure estimate,
            # and conservatively use the larger of signal entry and mark price.
            try:
                mark_response = self._get(
                    "/fapi/v1/premiumIndex", params={"symbol": intent.symbol}
                )
                mark_response.raise_for_status()
                mark_payload = mark_response.json()
                if (
                    not isinstance(mark_payload, dict)
                    or mark_payload.get("symbol") != intent.symbol
                ):
                    raise ValueError("mark_price_symbol_mismatch")
                mark_price = Decimal(str(mark_payload.get("markPrice", "NaN")))
                if not mark_price.is_finite() or mark_price <= 0:
                    raise ValueError("invalid_mark_price")
            except Exception as exc:
                return {
                    "accepted": False,
                    "reason": "market_order_mark_price_requires_reconciliation",
                    "requires_reconciliation": True,
                    "exception_type": type(exc).__name__,
                    "client_order_id": client_order_id,
                }
            # The signal entry may be stale. Revalidate stop/target geometry
            # and risk at the mark price before treating the market entry as
            # eligible; the order's actual fill can still move after this read.
            try:
                entry_price = Decimal(str(intent.entry))
                stop_price = Decimal(str(intent.stop_loss))
                target_price = Decimal(str(intent.take_profit))
                proposed_notional = max(entry_price, mark_price) * quantity
                mark_risk = abs(mark_price - stop_price) * quantity
                max_order_notional = Decimal(str(config.max_notional_usdt))
                max_risk_fraction = Decimal(str(config.max_risk_fraction))
                if intent.side == "BUY":
                    geometry_valid = stop_price < mark_price < target_price
                else:
                    geometry_valid = target_price < mark_price < stop_price
                if not geometry_valid:
                    return {
                        "accepted": False,
                        "reason": "market_order_mark_price_invalidates_risk_geometry",
                        "requires_reconciliation": True,
                        "client_order_id": client_order_id,
                    }
                if (
                    not proposed_notional.is_finite()
                    or proposed_notional <= 0
                    or proposed_notional > max_order_notional
                ):
                    return {
                        "accepted": False,
                        "reason": "market_order_mark_price_exceeds_order_notional_limit",
                        "requires_reconciliation": True,
                        "client_order_id": client_order_id,
                    }
                if (
                    not mark_risk.is_finite()
                    or mark_risk <= 0
                    or mark_risk > proposed_notional * max_risk_fraction
                ):
                    return {
                        "accepted": False,
                        "reason": "market_order_mark_price_exceeds_risk_limit",
                        "requires_reconciliation": True,
                        "client_order_id": client_order_id,
                    }
            except (InvalidOperation, TypeError, ValueError, OverflowError):
                return {
                    "accepted": False,
                    "reason": "market_order_mark_price_risk_calculation_failed",
                    "requires_reconciliation": True,
                    "client_order_id": client_order_id,
                }
            try:
                open_order_metrics = self.open_entry_order_notional()
            except Exception as exc:
                return {
                    "accepted": False,
                    "reason": "open_entry_orders_preflight_exception_requires_reconciliation",
                    "requires_reconciliation": True,
                    "exception_type": type(exc).__name__,
                }
            if (
                not isinstance(open_order_metrics, dict)
                or open_order_metrics.get("ok") is not True
                or open_order_metrics.get("mode") != "testnet_open_entry_order_notional"
            ):
                return {
                    "accepted": False,
                    "reason": "open_entry_orders_preflight_failed_requires_reconciliation",
                    "requires_reconciliation": True,
                    "open_orders_reason": (
                        open_order_metrics.get("reason") if isinstance(open_order_metrics, dict)
                        else "invalid_open_orders_response"
                    ),
                }
            try:
                current_position_notional = Decimal(str(risk_metrics["total_notional_usdt"]))
                current_open_order_notional = Decimal(str(open_order_metrics["open_entry_order_notional_usdt"]))
                current_account_notional = current_position_notional + current_open_order_notional
                account_notional_limit = Decimal(str(config.max_account_notional_usdt))
                projected_account_notional = current_account_notional + proposed_notional
                if (
                    not current_position_notional.is_finite()
                    or current_position_notional < 0
                    or not current_open_order_notional.is_finite()
                    or current_open_order_notional < 0
                    or not account_notional_limit.is_finite()
                    or account_notional_limit <= 0
                    or not proposed_notional.is_finite()
                    or proposed_notional <= 0
                    or not projected_account_notional.is_finite()
                ):
                    raise InvalidOperation
            except (KeyError, InvalidOperation, TypeError, ValueError, OverflowError):
                return {
                    "accepted": False,
                    "reason": "invalid_account_risk_preflight_values_requires_reconciliation",
                    "requires_reconciliation": True,
                }
            if projected_account_notional > account_notional_limit:
                return {
                    "accepted": False,
                    "reason": "account_notional_limit_exceeded",
                    "current_account_notional_usdt": float(current_account_notional),
                    "current_position_notional_usdt": float(current_position_notional),
                    "current_open_entry_order_notional_usdt": float(current_open_order_notional),
                    "proposed_order_notional_usdt": float(proposed_notional),
                    "max_account_notional_usdt": float(account_notional_limit),
                }
            # Reserve account capacity atomically across adapter instances and
            # processes before the exchange POST. Existing reservations for the
            # same client ID are never permission to blindly retry.
            try:
                reservation = self._risk_ledger.reserve(
                    client_order_id,
                    proposed_notional,
                    current_account_notional,
                    account_notional_limit,
                )
            except RiskLedgerError as exc:
                return {
                    "accepted": False,
                    "reason": "durable_risk_reservation_failed_requires_reconciliation",
                    "requires_reconciliation": True,
                    "exception_type": type(exc).__name__,
                    "client_order_id": client_order_id,
                }
            if not isinstance(reservation, dict) or reservation.get("ok") is not True:
                return {
                    "accepted": False,
                    "reason": (
                        reservation.get("reason", "invalid_risk_reservation_result")
                        if isinstance(reservation, dict)
                        else "invalid_risk_reservation_result"
                    ),
                    "requires_reconciliation": True,
                    "client_order_id": client_order_id,
                    "reservation": reservation if isinstance(reservation, dict) else None,
                }
            if reservation.get("reason") != "risk_reserved":
                return {
                    "accepted": False,
                    "reason": "existing_risk_reservation_requires_reconciliation",
                    "requires_reconciliation": True,
                    "client_order_id": client_order_id,
                    "reservation_state": reservation.get("state"),
                }
        except (ValueError, InvalidOperation, TypeError, KeyError):
            return {"accepted": False, "reason": "exchange_preflight_failed"}
        try:
            state = self._risk_ledger.transition(
                client_order_id, "SUBMITTING",
                detail="durable reservation acquired; exchange submission about to begin",
            )
            if not isinstance(state, dict) or state.get("ok") is not True:
                return {
                    "accepted": False,
                    "reason": "risk_reservation_transition_failed_requires_reconciliation",
                    "requires_reconciliation": True,
                    "client_order_id": client_order_id,
                }
        except RiskLedgerError as exc:
            return {
                "accepted": False,
                "reason": "risk_reservation_transition_failed_requires_reconciliation",
                "requires_reconciliation": True,
                "exception_type": type(exc).__name__,
                "client_order_id": client_order_id,
            }
        params = self._signed_params({
            "symbol": intent.symbol,
            "side": intent.side,
            "type": "MARKET",
            "quantity": quantity_text,
            "newClientOrderId": client_order_id,
        })
        try:
            response = self._post("/fapi/v1/order", params=params)
            response.raise_for_status()
            payload = response.json()
        except (httpx.TimeoutException, httpx.TransportError):
            # A timeout after POST is ambiguous: retain the reservation and
            # mark the outcome unknown. Never auto-retry an order.
            try:
                self._risk_ledger.transition(
                    client_order_id, "UNKNOWN", detail="ambiguous timeout/transport error after POST"
                )
            except RiskLedgerError:
                pass
            return {
                "accepted": False,
                "reason": "testnet_order_outcome_unknown",
                "requires_reconciliation": True,
                "client_order_id": client_order_id,
            }
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500:
                try:
                    self._risk_ledger.transition(
                        client_order_id, "UNKNOWN", detail="HTTP server error; order outcome ambiguous"
                    )
                except RiskLedgerError:
                    pass
                return {
                    "accepted": False,
                    "reason": "testnet_order_outcome_unknown",
                    "requires_reconciliation": True,
                    "client_order_id": client_order_id,
                }
            # Binance -4116 means this client ID is already in use. The order
            # may belong to a prior attempt that reached the exchange; don't
            # classify it as a clean rejection or let callers blindly retry.
            error_payload = {}
            try:
                parsed_payload = exc.response.json()
                if isinstance(parsed_payload, dict):
                    error_payload = parsed_payload
            except (ValueError, TypeError):
                pass
            error_code = error_payload.get("code")
            error_message = error_payload.get("msg", "")
            duplicate_client_id = (
                str(error_code) == "-4116"
                or (
                    isinstance(error_message, str)
                    and "client order id" in error_message.lower()
                    and (
                        "not unique" in error_message.lower()
                        or "already exists" in error_message.lower()
                    )
                )
            )
            if duplicate_client_id:
                try:
                    self._risk_ledger.transition(
                        client_order_id, "UNKNOWN", detail="client order ID conflict requires exchange reconciliation"
                    )
                except RiskLedgerError:
                    pass
                return {
                    "accepted": False,
                    "reason": "testnet_order_client_id_conflict_requires_reconciliation",
                    "requires_reconciliation": True,
                    "http_status": exc.response.status_code,
                    "client_order_id": client_order_id,
                }
            try:
                self._risk_ledger.transition(
                    client_order_id, "RELEASED",
                    detail=f"exchange returned definitive HTTP rejection {exc.response.status_code}",
                )
            except RiskLedgerError:
                return {
                    "accepted": False,
                    "reason": "testnet_order_rejected_risk_release_requires_reconciliation",
                    "requires_reconciliation": True,
                    "http_status": exc.response.status_code,
                    "client_order_id": client_order_id,
                }
            return {
                "accepted": False,
                "reason": "testnet_order_rejected",
                "http_status": exc.response.status_code,
                "client_order_id": client_order_id,
            }
        except (ValueError, TypeError):
            try:
                self._risk_ledger.transition(
                    client_order_id, "UNKNOWN", detail="invalid or missing POST response; reconcile exchange state"
                )
            except RiskLedgerError:
                pass
            return {
                "accepted": False,
                "reason": "testnet_order_outcome_unknown",
                "requires_reconciliation": True,
                "client_order_id": client_order_id,
            }
        except Exception as exc:
            # Unexpected custom-client failures can happen after the exchange
            # receives the request. Treat the outcome as ambiguous; never retry.
            try:
                self._risk_ledger.transition(
                    client_order_id, "UNKNOWN", detail="unexpected POST exception; reconcile exchange state"
                )
            except RiskLedgerError:
                pass
            return {
                "accepted": False,
                "reason": "testnet_order_outcome_unknown",
                "requires_reconciliation": True,
                "exception_type": type(exc).__name__,
                "client_order_id": client_order_id,
            }
        if (
            not isinstance(payload, dict)
            or not _valid_exchange_order_id(payload.get("orderId"))
            or payload.get("symbol") != intent.symbol
            or payload.get("clientOrderId") != client_order_id
            or payload.get("status") not in {"NEW", "PARTIALLY_FILLED", "FILLED"}
        ):
            try:
                self._risk_ledger.transition(
                    client_order_id, "UNKNOWN", detail="unexpected order response; reconcile exchange state"
                )
            except RiskLedgerError:
                pass
            return {
                "accepted": False,
                "reason": "unexpected_testnet_order_response",
                "requires_reconciliation": True,
                "client_order_id": client_order_id,
            }

        # A matching order ID/status is not enough: validate the exchange's
        # quantity fields before callers decide whether protection is needed.
        # Missing or contradictory fill data is ambiguous and must be reconciled.
        try:
            original_quantity = Decimal(str(payload.get("origQty", "NaN")))
            executed_quantity = Decimal(str(payload.get("executedQty", "NaN")))
            requested_quantity = Decimal(quantity_text)
            if (
                not original_quantity.is_finite()
                or not executed_quantity.is_finite()
                or original_quantity <= 0
                or original_quantity != requested_quantity
                or executed_quantity < 0
                or executed_quantity > original_quantity
            ):
                raise InvalidOperation
            status = payload["status"]
            if (
                (status == "NEW" and executed_quantity != 0)
                or (
                    status == "PARTIALLY_FILLED"
                    and not (Decimal("0") < executed_quantity < original_quantity)
                )
                or (
                    status == "FILLED"
                    and executed_quantity != original_quantity
                )
            ):
                raise InvalidOperation
        except (InvalidOperation, TypeError, ValueError, KeyError):
            try:
                self._risk_ledger.transition(
                    client_order_id, "UNKNOWN", detail="order fill fields contradictory; reconcile exchange state"
                )
            except RiskLedgerError:
                pass
            return {
                "accepted": False,
                "reason": "testnet_order_fill_state_requires_reconciliation",
                "requires_reconciliation": True,
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
        if not isinstance(symbol, str) or not symbol.isascii() or not symbol.endswith("USDT") or not symbol[:-4].isalnum():
            return {"found": False, "reason": "invalid_symbol"}
        if not isinstance(client_order_id, str) or not client_order_id or len(client_order_id) > 36:
            return {"found": False, "reason": "invalid_client_order_id"}
        if not client_order_id.isascii() or not client_order_id.replace("-", "").replace("_", "").isalnum():
            return {"found": False, "reason": "invalid_client_order_id"}
        try:
            response = self._get(
                "/fapi/v1/order",
                params=self._signed_params({
                    "symbol": symbol,
                    "origClientOrderId": client_order_id,
                }),
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.TimeoutException, httpx.TransportError):
            # A failed read does not establish whether an order exists or what
            # state it is in. The caller must reconcile from a fresh exchange
            # snapshot; never interpret a transport failure as absence.
            return {
                "found": False,
                "reason": "testnet_order_query_outcome_unknown",
                "requires_reconciliation": True,
                "symbol": symbol,
                "client_order_id": client_order_id,
            }
        except httpx.HTTPStatusError as exc:
            # Includes 404 (-2013) and server errors. A not-found response can
            # race with a just-accepted order, so it is not proof of absence.
            return {
                "found": False,
                "reason": "testnet_order_query_http_outcome_requires_reconciliation",
                "requires_reconciliation": True,
                "http_status": exc.response.status_code,
                "symbol": symbol,
                "client_order_id": client_order_id,
            }
        except (ValueError, TypeError):
            # Unreadable JSON / malformed response is ambiguous, not "not found".
            return {
                "found": False,
                "reason": "testnet_order_query_response_unreadable_requires_reconciliation",
                "requires_reconciliation": True,
                "symbol": symbol,
                "client_order_id": client_order_id,
            }
        except Exception as exc:
            # A custom client/adapter failure also cannot prove order absence.
            return {
                "found": False,
                "reason": "testnet_order_query_unexpected_error_requires_reconciliation",
                "requires_reconciliation": True,
                "exception_type": type(exc).__name__,
                "symbol": symbol,
                "client_order_id": client_order_id,
            }
        if (
            not isinstance(payload, dict)
            or not _valid_exchange_order_id(payload.get("orderId"))
            or payload.get("symbol") != symbol
            or payload.get("clientOrderId") != client_order_id
            or not isinstance(payload.get("status"), str)
            or payload.get("status") not in {
                "NEW", "PARTIALLY_FILLED", "FILLED", "CANCELED", "REJECTED", "EXPIRED"
            }
            or "executedQty" not in payload
            or "origQty" not in payload
        ):
            return {
                "found": False,
                "reason": "unexpected_or_mismatched_testnet_order_response",
                "requires_reconciliation": True,
            }
        try:
            original_quantity = Decimal(str(payload["origQty"]))
            executed_quantity = Decimal(str(payload["executedQty"]))
            average_price = Decimal(str(payload.get("avgPrice", "0")))
            if (
                not original_quantity.is_finite()
                or not executed_quantity.is_finite()
                or not average_price.is_finite()
                or original_quantity <= 0
                or executed_quantity < 0
                or executed_quantity > original_quantity
                or average_price < 0
            ):
                raise InvalidOperation
        except (InvalidOperation, TypeError, ValueError):
            return {
                "found": False,
                "reason": "invalid_testnet_order_numeric_fields",
                "requires_reconciliation": True,
            }
        if payload["status"] == "NEW" and executed_quantity != 0:
            return {
                "found": False,
                "reason": "new_testnet_order_has_execution_requires_reconciliation",
                "requires_reconciliation": True,
            }
        if payload["status"] == "PARTIALLY_FILLED" and not (
            0 < executed_quantity < original_quantity
        ):
            return {
                "found": False,
                "reason": "partially_filled_testnet_order_quantity_mismatch",
                "requires_reconciliation": True,
            }
        if payload["status"] == "FILLED" and executed_quantity != original_quantity:
            return {
                "found": False,
                "reason": "filled_testnet_order_quantity_mismatch",
                "requires_reconciliation": True,
            }
        return {
            "found": True,
            "mode": "testnet_order_reconciliation",
            "symbol": payload["symbol"],
            "client_order_id": payload["clientOrderId"],
            "exchange_order_id": str(payload["orderId"]),
            "status": payload["status"],
            "executed_quantity": str(executed_quantity),
            "original_quantity": str(original_quantity),
            "average_price": str(average_price),
        }

    def testnet_position_snapshot(self, symbol: str):
        """Read authoritative TESTNET position and open orders; no mutations."""
        if not isinstance(symbol, str) or not symbol.isascii() or not symbol.endswith("USDT") or not symbol[:-4].isalnum():
            return {"ok": False, "reason": "invalid_symbol"}
        # Bound the observation window across both account and open-order reads.
        # These timestamps do not make the exchange reads atomic; callers must
        # still reconcile and reject snapshots whose window is stale or too wide.
        snapshot_started_at_ms = int(time.time() * 1000)
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
        # Do not infer a flat position from a missing positionAmt, or treat a
        # missing entryPrice as zero. A malformed account payload is not an
        # authoritative position snapshot and must fail closed.
        if (
            "positionAmt" not in position
            or "entryPrice" not in position
            or position.get("positionAmt") in (None, "")
            or position.get("entryPrice") in (None, "")
        ):
            return {"ok": False, "reason": "invalid_testnet_position_values"}
        try:
            amount = Decimal(str(position["positionAmt"]))
            entry_price = Decimal(str(position["entryPrice"]))
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
        required_order_fields = (
            "orderId", "symbol", "clientOrderId", "side", "type", "status",
            "reduceOnly", "stopPrice", "origQty", "executedQty",
        )
        for order in orders:
            if any(field not in order for field in required_order_fields):
                return {"ok": False, "reason": "invalid_testnet_open_order_record"}
            if order.get("symbol") != symbol:
                return {"ok": False, "reason": "testnet_open_order_symbol_mismatch"}
            if (
                not _valid_exchange_order_id(order["orderId"])
                or not isinstance(order["clientOrderId"], str)
                or not order["clientOrderId"]
                or len(order["clientOrderId"]) > 36
                or not order["clientOrderId"].isascii()
                or not order["clientOrderId"].replace("-", "").replace("_", "").isalnum()
            ):
                return {"ok": False, "reason": "invalid_testnet_open_order_identity"}
            if not isinstance(order["reduceOnly"], bool):
                return {"ok": False, "reason": "invalid_testnet_open_order_reduce_only"}
            if not all(isinstance(order[field], str) and order[field] for field in ("side", "type", "status")):
                return {"ok": False, "reason": "invalid_testnet_open_order_record"}
            if order["side"] not in {"BUY", "SELL"}:
                return {"ok": False, "reason": "invalid_testnet_open_order_side"}
            if order["status"] not in {"NEW", "PARTIALLY_FILLED"}:
                return {"ok": False, "reason": "invalid_testnet_open_order_status"}
            try:
                stop_price = Decimal(str(order["stopPrice"]))
                orig_quantity = Decimal(str(order["origQty"]))
                executed_quantity = Decimal(str(order["executedQty"]))
                if (
                    not stop_price.is_finite()
                    or not orig_quantity.is_finite()
                    or not executed_quantity.is_finite()
                    or stop_price < 0
                    or orig_quantity <= 0
                    or executed_quantity < 0
                    or executed_quantity > orig_quantity
                    or (order["status"] == "NEW" and executed_quantity != 0)
                    or (
                        order["status"] == "PARTIALLY_FILLED"
                        and not (0 < executed_quantity < orig_quantity)
                    )
                ):
                    raise InvalidOperation
            except (InvalidOperation, TypeError, ValueError):
                return {"ok": False, "reason": "invalid_testnet_open_order_numeric_fields"}
        snapshot_completed_at_ms = int(time.time() * 1000)
        return {
            "ok": True,
            "mode": "testnet_read_only_snapshot",
            "symbol": symbol,
            "snapshot_started_at_ms": snapshot_started_at_ms,
            "snapshot_completed_at_ms": snapshot_completed_at_ms,
            "snapshot_duration_ms": float(snapshot_completed_at_ms - snapshot_started_at_ms),
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
        try:
            snapshot = self.testnet_position_snapshot(symbol)
        except Exception as exc:
            # A snapshot transport/decoding failure leaves position and existing
            # protection state unknown. Do not continue toward a new order.
            return {
                "accepted": False,
                "reason": "position_snapshot_failed_requires_reconciliation",
                "requires_reconciliation": True,
                "exception_type": type(exc).__name__,
                "client_order_id": client_order_id,
            }
        if not isinstance(snapshot, dict) or not snapshot.get("ok"):
            return {
                "accepted": False,
                "reason": "position_snapshot_failed",
                "requires_reconciliation": True,
                "client_order_id": client_order_id,
            }
        # Position and open-order state are read in separate exchange calls, so
        # never submit a protective order from a snapshot with a missing,
        # inconsistent, or excessively wide observation window.
        try:
            started_ms = snapshot["snapshot_started_at_ms"]
            completed_ms = snapshot["snapshot_completed_at_ms"]
            duration_ms = Decimal(str(snapshot["snapshot_duration_ms"]))
            if (
                isinstance(started_ms, bool) or not isinstance(started_ms, int)
                or isinstance(completed_ms, bool) or not isinstance(completed_ms, int)
                or started_ms < 0 or completed_ms < started_ms
                or not duration_ms.is_finite() or duration_ms < 0
                or duration_ms != Decimal(completed_ms - started_ms)
                or duration_ms > MAX_PROTECTIVE_SNAPSHOT_DURATION_MS
            ):
                raise ValueError("invalid_or_stale_snapshot_window")
            position_amount = Decimal(snapshot["position_amount"])
            entry_price = Decimal(snapshot["entry_price"])
        except (InvalidOperation, TypeError, ValueError, KeyError):
            return {
                "accepted": False,
                "reason": "position_snapshot_stale_or_invalid",
                "requires_reconciliation": True,
                "client_order_id": client_order_id,
            }
        # A client order ID is an idempotency/recovery key. If it is already
        # present in the authoritative open-order snapshot, do not submit again:
        # the existing order may be the result of a previous ambiguous attempt.
        # This check is intentionally read-only and does not infer that other
        # order IDs are safe substitutes for this request.
        existing_client_order_ids = {
            order.get("client_order_id")
            for order in snapshot.get("open_orders", [])
            if isinstance(order, dict)
        }
        if client_order_id in existing_client_order_ids:
            return {
                "accepted": False,
                "reason": "protective_exit_client_id_already_open_requires_reconciliation",
                "requires_reconciliation": True,
                "client_order_id": client_order_id,
            }
        if (
            not position_amount.is_finite()
            or not entry_price.is_finite()
            or position_amount == 0
            or entry_price <= 0
        ):
            return {"accepted": False, "reason": "no_open_position_or_invalid_position_values"}
        is_long = position_amount > 0
        if exit_kind == "stop_loss":
            geometry_ok = trigger < entry_price if is_long else trigger > entry_price
            order_type = "STOP_MARKET"
        else:
            geometry_ok = trigger > entry_price if is_long else trigger < entry_price
            order_type = "TAKE_PROFIT_MARKET"
        # Do not stack multiple open orders of the same protective kind for one
        # position. An earlier order may already cover the requested exit; adding
        # another full-position conditional order creates ambiguous protection.
        conflicting_orders = [
            order for order in snapshot.get("open_orders", [])
            if isinstance(order, dict)
            and order.get("side") == ("SELL" if is_long else "BUY")
            and order.get("type") == order_type
            and order.get("status") in {"NEW", "PARTIALLY_FILLED"}
        ]
        if conflicting_orders:
            return {
                "accepted": False,
                "reason": "protective_exit_existing_order_requires_reconciliation",
                "requires_reconciliation": True,
                "existing_client_order_ids": [
                    order.get("client_order_id") for order in conflicting_orders
                ],
                "client_order_id": client_order_id,
            }
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
            raw_tick = rules.get("price_tick_size")
            raw_min_price = rules.get("min_price")
            raw_max_price = rules.get("max_price")
            if raw_tick in (None, "") or raw_min_price in (None, "") or raw_max_price in (None, ""):
                return {"accepted": False, "reason": "exchange_price_filter_missing"}
            tick = Decimal(str(raw_tick))
            min_price = Decimal(str(raw_min_price))
            max_price = Decimal(str(raw_max_price))
            if (
                step <= 0 or minimum <= 0 or maximum < minimum
                or not tick.is_finite() or tick <= 0
                or not min_price.is_finite() or min_price < 0
                or not max_price.is_finite() or max_price <= min_price
            ):
                return {"accepted": False, "reason": "invalid_exchange_quantity_or_price_filters"}
            if trigger < min_price or trigger > max_price:
                return {"accepted": False, "reason": "trigger_price_outside_exchange_price_bounds"}
            if trigger % tick != 0:
                return {"accepted": False, "reason": "trigger_price_not_aligned_to_exchange_tick"}
            rounded = (requested_quantity / step).to_integral_value(rounding=ROUND_DOWN) * step
            if rounded < minimum:
                return {"accepted": False, "reason": "quantity_below_exchange_minimum"}
            if rounded > maximum or rounded > abs(position_amount):
                return {"accepted": False, "reason": "exit_quantity_exceeds_position_or_exchange_maximum"}
            # Protective exits must cover the entire current position exactly.
            # Rounding down a trigger order would silently leave residual exposure.
            if rounded != requested_quantity or rounded != abs(position_amount):
                return {"accepted": False, "reason": "protective_exit_must_match_full_position_quantity"}
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
        # Conditional exits use MARK_PRICE as their trigger source. Entry-price
        # geometry alone is insufficient if the market has already crossed the
        # trigger; read the current mark price and fail closed if unavailable.
        try:
            mark_response = self._get(
                "/fapi/v1/premiumIndex",
                params={"symbol": symbol},
            )
            mark_response.raise_for_status()
            mark_payload = mark_response.json()
            if not isinstance(mark_payload, dict) or mark_payload.get("symbol") != symbol:
                raise ValueError("invalid_mark_price_response")
            mark_price = Decimal(str(mark_payload.get("markPrice", "NaN")))
            if not mark_price.is_finite() or mark_price <= 0:
                raise ValueError("invalid_mark_price")
        except Exception:
            # Unexpected client/response exceptions are also fail-closed: no
            # protective order is submitted without a validated mark price.
            return {
                "accepted": False,
                "reason": "mark_price_preflight_failed",
                "requires_reconciliation": True,
                "client_order_id": client_order_id,
            }
        if exit_kind == "stop_loss":
            mark_geometry_ok = trigger < mark_price if is_long else trigger > mark_price
        else:
            mark_geometry_ok = trigger > mark_price if is_long else trigger < mark_price
        if not mark_geometry_ok:
            return {
                "accepted": False,
                "reason": "protective_trigger_already_crossed",
                "mark_price": str(mark_price),
            }
        # A short snapshot collection window does not guarantee that the
        # snapshot is still fresh after exchange-filter and mark-price reads.
        snapshot_age_ms = int(time.time() * 1000) - completed_ms
        if snapshot_age_ms < 0 or snapshot_age_ms > MAX_PROTECTIVE_SNAPSHOT_AGE_MS:
            return {
                "accepted": False,
                "reason": "position_snapshot_stale_or_invalid",
                "requires_reconciliation": True,
                "client_order_id": client_order_id,
            }
        # Refresh the signature after the network preflight so its timestamp is
        # as close as possible to order submission (recvWindow is only 5 seconds).
        params = self._signed_params({
            key: value for key, value in params.items()
            if key not in {"timestamp", "signature"}
        })
        try:
            response = self._post("/fapi/v1/order", params=params)
            response.raise_for_status()
            payload = response.json()
        except (httpx.TimeoutException, httpx.TransportError):
            # The exchange may have accepted the conditional order. Reconcile by
            # client ID and exchange snapshot; do not retry this submission.
            return {
                "accepted": False,
                "reason": "protective_exit_outcome_unknown",
                "requires_reconciliation": True,
                "client_order_id": client_order_id,
            }
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500:
                return {
                    "accepted": False,
                    "reason": "protective_exit_outcome_unknown",
                    "requires_reconciliation": True,
                    "client_order_id": client_order_id,
                }
            # Binance USD-M Futures error -4116 means the client order ID is
            # already in use. That is not proof this submission is safely
            # rejected: an earlier attempt may have reached the exchange.
            # Reconcile by client ID and exchange snapshots; never retry here.
            error_payload = {}
            try:
                parsed_payload = exc.response.json()
                if isinstance(parsed_payload, dict):
                    error_payload = parsed_payload
            except (ValueError, TypeError):
                pass
            error_code = error_payload.get("code")
            error_message = error_payload.get("msg", "")
            duplicate_client_id = (
                str(error_code) == "-4116"
                or (
                    isinstance(error_message, str)
                    and "client order id" in error_message.lower()
                    and ("not unique" in error_message.lower() or "already exists" in error_message.lower())
                )
            )
            if duplicate_client_id:
                return {
                    "accepted": False,
                    "reason": "protective_exit_client_id_conflict_requires_reconciliation",
                    "requires_reconciliation": True,
                    "http_status": exc.response.status_code,
                    "client_order_id": client_order_id,
                }
            return {
                "accepted": False,
                "reason": "protective_exit_rejected",
                "http_status": exc.response.status_code,
                "client_order_id": client_order_id,
            }
        except (ValueError, TypeError):
            return {
                "accepted": False,
                "reason": "protective_exit_outcome_unknown",
                "requires_reconciliation": True,
                "client_order_id": client_order_id,
            }
        except Exception as exc:
            # Custom clients/adapters can fail outside the expected httpx
            # hierarchy after the request has been sent. The exchange may have
            # accepted the order, so classify this as ambiguous and never retry.
            return {
                "accepted": False,
                "reason": "protective_exit_outcome_unknown",
                "requires_reconciliation": True,
                "exception_type": type(exc).__name__,
                "client_order_id": client_order_id,
            }
        # A successful HTTP response is not sufficient: verify that the
        # exchange echoed the exact protective order we intended to place.
        # Missing/mismatched fields may mean the response cannot safely identify
        # the protection that is actually active; reconcile by client ID.
        if (
            not isinstance(payload, dict)
            or not _valid_exchange_order_id(payload.get("orderId"))
            or payload.get("symbol") != symbol
            or payload.get("clientOrderId") != client_order_id
            or payload.get("side") != params["side"]
            or payload.get("type") != order_type
            or payload.get("reduceOnly") is not True
            or payload.get("workingType") != "MARK_PRICE"
        ):
            return {
                "accepted": False,
                "reason": "unexpected_testnet_order_response",
                "requires_reconciliation": True,
                "client_order_id": client_order_id,
            }
        try:
            response_quantity = Decimal(str(payload.get("origQty", "NaN")))
            response_trigger = Decimal(str(payload.get("stopPrice", "NaN")))
            if (
                not response_quantity.is_finite()
                or response_quantity != rounded
                or not response_trigger.is_finite()
                or response_trigger != trigger
            ):
                raise InvalidOperation
        except (InvalidOperation, TypeError, ValueError):
            return {
                "accepted": False,
                "reason": "unexpected_testnet_order_response",
                "requires_reconciliation": True,
                "client_order_id": client_order_id,
            }
        # Conditional exits should initially be resting. A response that is
        # already partially filled/filled is an execution race, not a verified
        # installation; require a fresh exchange snapshot and manual reconciliation.
        if payload.get("status") != "NEW":
            return {
                "accepted": False,
                "reason": "protective_exit_submission_state_requires_reconciliation",
                "requires_reconciliation": True,
                "client_order_id": client_order_id,
                "status": payload.get("status"),
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
        if not isinstance(symbol, str) or not symbol.isascii() or not symbol.endswith("USDT") or not symbol[:-4].isalnum():
            return {"cancelled": False, "reason": "invalid_symbol"}
        if not isinstance(client_order_id, str) or not client_order_id or len(client_order_id) > 36:
            return {"cancelled": False, "reason": "invalid_client_order_id"}
        if not client_order_id.isascii() or not client_order_id.replace("-", "").replace("_", "").isalnum():
            return {"cancelled": False, "reason": "invalid_client_order_id"}
        try:
            response = self._delete(
                "/fapi/v1/order",
                params=self._signed_params({
                    "symbol": symbol,
                    "origClientOrderId": client_order_id,
                }),
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.TimeoutException, httpx.TransportError):
            # The cancel may have reached the exchange even if its response did
            # not reach us. Never retry; query the order and account snapshots.
            return {
                "cancelled": False,
                "reason": "testnet_cancel_outcome_unknown",
                "requires_reconciliation": True,
                "client_order_id": client_order_id,
            }
        except httpx.HTTPStatusError as exc:
            # Includes 404 (possibly already filled/cancelled) and 5xx: neither
            # proves the order's final state. Do not retry the mutation.
            return {
                "cancelled": False,
                "reason": "testnet_cancel_http_outcome_requires_reconciliation",
                "requires_reconciliation": True,
                "http_status": exc.response.status_code,
                "client_order_id": client_order_id,
            }
        except (ValueError, TypeError):
            return {
                "cancelled": False,
                "reason": "testnet_cancel_response_unreadable_requires_reconciliation",
                "requires_reconciliation": True,
                "client_order_id": client_order_id,
            }
        except Exception as exc:
            # Unexpected client/transport adapter failures are ambiguous too:
            # the request may have reached the exchange. Never retry a cancel.
            return {
                "cancelled": False,
                "reason": "testnet_cancel_unexpected_error_requires_reconciliation",
                "requires_reconciliation": True,
                "exception_type": type(exc).__name__,
                "client_order_id": client_order_id,
            }
        if (
            not isinstance(payload, dict)
            or not _valid_exchange_order_id(payload.get("orderId"))
            or payload.get("symbol") != symbol
            or payload.get("clientOrderId") != client_order_id
            or not isinstance(payload.get("status"), str)
            or payload.get("status") not in {
                "NEW", "PARTIALLY_FILLED", "FILLED", "CANCELED", "REJECTED", "EXPIRED"
            }
        ):
            return {
                "cancelled": False,
                "reason": "unexpected_or_mismatched_testnet_cancel_response",
                "requires_reconciliation": True,
                "client_order_id": client_order_id,
            }
        if payload["status"] != "CANCELED":
            return {
                "cancelled": False,
                "reason": "testnet_cancel_not_confirmed_requires_reconciliation",
                "requires_reconciliation": True,
                "symbol": payload["symbol"],
                "client_order_id": payload["clientOrderId"],
                "exchange_order_id": str(payload["orderId"]),
                "status": payload["status"],
            }
        return {
            "cancelled": True,
            "mode": "testnet_order_cancelled",
            "symbol": payload["symbol"],
            "client_order_id": payload["clientOrderId"],
            "exchange_order_id": str(payload["orderId"]),
            "status": payload["status"],
        }

    def close(self):
        self._client.close()
