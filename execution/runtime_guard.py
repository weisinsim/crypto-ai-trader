"""Fail-closed runtime risk gate for paper/testnet execution.

The caller must supply authoritative account/day state; this module deliberately
does not persist state or place orders. Missing, stale, or malformed state blocks.
"""
from dataclasses import dataclass
import math
from typing import Optional


@dataclass(frozen=True)
class RuntimeRiskConfig:
    max_daily_loss_usdt: float = 10.0
    max_open_positions: int = 1
    max_symbol_notional_usdt: float = 100.0
    max_total_notional_usdt: float = 100.0
    max_consecutive_execution_errors: int = 3


@dataclass(frozen=True)
class RuntimeSnapshot:
    daily_realized_pnl_usdt: float
    open_positions: int
    symbol_notional_usdt: float
    total_notional_usdt: float
    consecutive_execution_errors: int
    emergency_stop: bool = False
    snapshot_age_seconds: float = 0.0


@dataclass(frozen=True)
class RuntimeRiskDecision:
    allowed: bool
    reason: str


def validate_runtime_state(
    snapshot: Optional[RuntimeSnapshot],
    config: RuntimeRiskConfig = RuntimeRiskConfig(),
    max_snapshot_age_seconds: float = 30.0,
) -> RuntimeRiskDecision:
    """Fail closed if kill switch, daily loss, exposure, errors, or state freshness fail."""
    if snapshot is None:
        return RuntimeRiskDecision(False, "runtime_snapshot_missing")
    try:
        config_numbers = (
            float(config.max_daily_loss_usdt),
            float(config.max_symbol_notional_usdt),
            float(config.max_total_notional_usdt),
            float(max_snapshot_age_seconds),
        )
    except (AttributeError, TypeError, ValueError, OverflowError):
        return RuntimeRiskDecision(False, "invalid_runtime_risk_configuration")
    try:
        if any(not math.isfinite(value) for value in config_numbers):
            return RuntimeRiskDecision(False, "invalid_runtime_risk_configuration")
        if (
            config_numbers[0] <= 0
            or isinstance(config.max_open_positions, bool)
            or not isinstance(config.max_open_positions, int)
            or config.max_open_positions < 0
            or config_numbers[1] <= 0
            or config_numbers[2] <= 0
            or isinstance(config.max_consecutive_execution_errors, bool)
            or not isinstance(config.max_consecutive_execution_errors, int)
            or config.max_consecutive_execution_errors < 0
            or config_numbers[3] < 0
        ):
            return RuntimeRiskDecision(False, "invalid_runtime_risk_configuration")

        if (
            isinstance(snapshot.open_positions, bool)
            or not isinstance(snapshot.open_positions, int)
            or isinstance(snapshot.consecutive_execution_errors, bool)
            or not isinstance(snapshot.consecutive_execution_errors, int)
            or not isinstance(snapshot.emergency_stop, bool)
        ):
            return RuntimeRiskDecision(False, "runtime_snapshot_invalid")
        pnl, symbol_notional, total_notional, age = (
            float(snapshot.daily_realized_pnl_usdt),
            float(snapshot.symbol_notional_usdt),
            float(snapshot.total_notional_usdt),
            float(snapshot.snapshot_age_seconds),
        )
    except (AttributeError, TypeError, ValueError, OverflowError):
        return RuntimeRiskDecision(False, "runtime_snapshot_invalid")

    if any(not math.isfinite(value) for value in (pnl, symbol_notional, total_notional, age)):
        return RuntimeRiskDecision(False, "runtime_snapshot_non_finite")
    if (
        snapshot.open_positions < 0
        or snapshot.consecutive_execution_errors < 0
        or symbol_notional < 0
        or total_notional < 0
        or age < 0
    ):
        return RuntimeRiskDecision(False, "runtime_snapshot_invalid")
    if snapshot.emergency_stop:
        return RuntimeRiskDecision(False, "emergency_stop_active")
    if age > config_numbers[3]:
        return RuntimeRiskDecision(False, "runtime_snapshot_stale")
    if pnl <= -config_numbers[0]:
        return RuntimeRiskDecision(False, "daily_loss_limit_reached")
    if snapshot.open_positions >= config.max_open_positions:
        return RuntimeRiskDecision(False, "open_position_limit_reached")
    if symbol_notional >= config_numbers[1]:
        return RuntimeRiskDecision(False, "symbol_notional_limit_reached")
    if total_notional >= config_numbers[2]:
        return RuntimeRiskDecision(False, "total_notional_limit_reached")
    if snapshot.consecutive_execution_errors >= config.max_consecutive_execution_errors:
        return RuntimeRiskDecision(False, "execution_error_circuit_breaker_open")
    return RuntimeRiskDecision(True, "runtime_risk_checks_passed")
