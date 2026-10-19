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
    if (
        not math.isfinite(config.max_daily_loss_usdt)
        or config.max_daily_loss_usdt <= 0
        or config.max_open_positions < 0
        or not math.isfinite(config.max_symbol_notional_usdt)
        or config.max_symbol_notional_usdt <= 0
        or not math.isfinite(config.max_total_notional_usdt)
        or config.max_total_notional_usdt <= 0
        or config.max_consecutive_execution_errors < 0
        or not math.isfinite(max_snapshot_age_seconds)
        or max_snapshot_age_seconds < 0
    ):
        return RuntimeRiskDecision(False, "invalid_runtime_risk_configuration")
    numeric = (
        snapshot.daily_realized_pnl_usdt,
        snapshot.symbol_notional_usdt,
        snapshot.total_notional_usdt,
        snapshot.snapshot_age_seconds,
    )
    if any(not math.isfinite(v) for v in numeric):
        return RuntimeRiskDecision(False, "runtime_snapshot_non_finite")
    if (
        snapshot.open_positions < 0
        or snapshot.consecutive_execution_errors < 0
        or snapshot.symbol_notional_usdt < 0
        or snapshot.total_notional_usdt < 0
        or snapshot.snapshot_age_seconds < 0
    ):
        return RuntimeRiskDecision(False, "runtime_snapshot_invalid")
    if snapshot.emergency_stop:
        return RuntimeRiskDecision(False, "emergency_stop_active")
    if snapshot.snapshot_age_seconds > max_snapshot_age_seconds:
        return RuntimeRiskDecision(False, "runtime_snapshot_stale")
    if snapshot.daily_realized_pnl_usdt <= -config.max_daily_loss_usdt:
        return RuntimeRiskDecision(False, "daily_loss_limit_reached")
    if snapshot.open_positions >= config.max_open_positions:
        return RuntimeRiskDecision(False, "open_position_limit_reached")
    if snapshot.symbol_notional_usdt >= config.max_symbol_notional_usdt:
        return RuntimeRiskDecision(False, "symbol_notional_limit_reached")
    if snapshot.total_notional_usdt >= config.max_total_notional_usdt:
        return RuntimeRiskDecision(False, "total_notional_limit_reached")
    if snapshot.consecutive_execution_errors >= config.max_consecutive_execution_errors:
        return RuntimeRiskDecision(False, "execution_error_circuit_breaker_open")
    return RuntimeRiskDecision(True, "runtime_risk_checks_passed")
