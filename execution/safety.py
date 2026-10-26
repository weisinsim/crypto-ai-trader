"""Fail-closed execution guardrails for the future order adapter.

This module intentionally does not place orders. It validates an execution
intent before a separate paper/testnet adapter can act on it. Real-money
execution must not be enabled until full replay parity, testnet verification,
and an explicit operator approval are complete.
"""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class ExecutionConfig:
    mode: str = "paper"
    live_trading_enabled: bool = False
    max_notional_usdt: float = 100.0
    max_risk_fraction: float = 0.005
    max_leverage: int = 2
    max_signal_age_seconds: int = 3600


@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    side: str
    entry: float
    stop_loss: float
    take_profit: float
    quantity: float
    signal_timestamp: int
    now_timestamp: int
    leverage: int = 1
    reduce_only: bool = False


@dataclass(frozen=True)
class ExecutionDecision:
    allowed: bool
    reason: str
    notional_usdt: float = 0.0
    risk_usdt: float = 0.0


def validate_order_intent(intent: OrderIntent, config: ExecutionConfig) -> ExecutionDecision:
    """Validate a proposed entry order; never authorizes real-money trading."""
    if not isinstance(intent, OrderIntent):
        return ExecutionDecision(False, "invalid_intent")
    if not isinstance(config, ExecutionConfig):
        return ExecutionDecision(False, "invalid_execution_config")
    if not isinstance(config.mode, str) or config.mode not in {"paper", "testnet"}:
        return ExecutionDecision(False, "unsupported_mode")
    if config.live_trading_enabled is not False:
        return ExecutionDecision(False, "live_trading_not_implemented")
    if not isinstance(intent.symbol, str) or not intent.symbol.isascii() or not intent.symbol.endswith("USDT") or not intent.symbol[:-4].isalnum():
        return ExecutionDecision(False, "invalid_symbol")
    if not isinstance(intent.side, str) or intent.side not in {"BUY", "SELL"}:
        return ExecutionDecision(False, "invalid_side")
    if intent.reduce_only is not False:
        return ExecutionDecision(False, "reduce_only_entry_not_supported")

    numeric = (
        intent.entry, intent.stop_loss, intent.take_profit, intent.quantity,
        config.max_notional_usdt, config.max_risk_fraction,
    )
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
        for value in numeric
    ):
        return ExecutionDecision(False, "non_positive_price_quantity_or_limit")
    if (
        isinstance(config.max_leverage, bool)
        or not isinstance(config.max_leverage, int)
        or config.max_leverage < 1
        or isinstance(config.max_signal_age_seconds, bool)
        or not isinstance(config.max_signal_age_seconds, int)
        or config.max_signal_age_seconds < 0
        or config.max_risk_fraction > 1
    ):
        return ExecutionDecision(False, "invalid_risk_configuration")
    if isinstance(intent.leverage, bool) or not isinstance(intent.leverage, int):
        return ExecutionDecision(False, "invalid_leverage")
    if intent.leverage < 1 or intent.leverage > config.max_leverage:
        return ExecutionDecision(False, "leverage_limit_exceeded")
    if (
        isinstance(intent.signal_timestamp, bool)
        or not isinstance(intent.signal_timestamp, int)
        or isinstance(intent.now_timestamp, bool)
        or not isinstance(intent.now_timestamp, int)
    ):
        return ExecutionDecision(False, "invalid_signal_timestamp")
    age = intent.now_timestamp - intent.signal_timestamp
    if age < 0 or age > config.max_signal_age_seconds:
        return ExecutionDecision(False, "stale_or_future_signal")
    if intent.side == "BUY":
        if not (intent.stop_loss < intent.entry < intent.take_profit):
            return ExecutionDecision(False, "invalid_long_risk_geometry")
    else:
        if not (intent.take_profit < intent.entry < intent.stop_loss):
            return ExecutionDecision(False, "invalid_short_risk_geometry")
    notional = intent.entry * intent.quantity
    risk = abs(intent.entry - intent.stop_loss) * intent.quantity
    if notional > config.max_notional_usdt:
        return ExecutionDecision(False, "notional_limit_exceeded", notional, risk)
    if risk > notional * config.max_risk_fraction:
        return ExecutionDecision(False, "risk_limit_exceeded", notional, risk)
    return ExecutionDecision(True, "paper_or_testnet_validation_passed", notional, risk)
