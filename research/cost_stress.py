"""Full simulator reruns under explicit transaction-cost stress assumptions."""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any

from research.backtest import run_ema_cross_backtest


def _valid_rate(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be finite and in [0, 1)")
    try:
        rate = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be finite and in [0, 1)") from exc
    if not math.isfinite(rate) or not 0 <= rate < 1:
        raise ValueError(f"{label} must be finite and in [0, 1)")
    return rate


def _multiplier(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be finite and >= 1")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be finite and >= 1") from exc
    if not math.isfinite(result) or result < 1:
        raise ValueError(f"{label} must be finite and >= 1")
    return result


def _metrics(result: Mapping[str, Any], fee_rate: float, slippage_rate: float) -> dict:
    return {
        "status": result.get("status", "UNKNOWN"),
        "fee_rate": fee_rate,
        "slippage_rate": slippage_rate,
        "trades": len(result.get("trade_log", [])),
        "net_return_pct": result.get("net_return_pct"),
        "max_drawdown_pct": result.get("max_drawdown_pct"),
        "profit_factor": result.get("profit_factor"),
        "equity_final": result.get("equity_final"),
        "equity_floor_hit": result.get("equity_floor_hit", False),
        "funding_cashflow_total": round(sum(
            float(trade.get("funding_cashflow", 0.0) or 0.0)
            for trade in result.get("trade_log", [])
        ), 10),
        "funding_settlement_count_total": sum(
            int(trade.get("funding_settlement_count", 0) or 0)
            for trade in result.get("trade_log", [])
        ),
    }


def run_cost_stress_scenarios(
    candles: Iterable[dict],
    *,
    base_fee_rate: float,
    base_slippage_rate: float,
    stress_multiplier: float = 1.5,
    severe_stress_multiplier: float = 2.0,
    backtest_kwargs: Mapping[str, Any] | None = None,
    trade_start_ts: int | None = None,
) -> dict:
    """Rerun identical data/strategy/funding with only fee and slippage rates changed.

    Multipliers are explicit scenario assumptions, not claims about venue-specific
    realized execution costs. The input candle/funding iterables are materialized
    once so each scenario receives the same observations.
    """
    base_fee = _valid_rate(base_fee_rate, "base_fee_rate")
    base_slippage = _valid_rate(base_slippage_rate, "base_slippage_rate")
    stress = _multiplier(stress_multiplier, "stress_multiplier")
    severe = _multiplier(severe_stress_multiplier, "severe_stress_multiplier")
    if severe < stress:
        raise ValueError("severe_stress_multiplier must be >= stress_multiplier")
    kwargs = dict(backtest_kwargs or {})
    if "fee_rate" in kwargs or "slippage_rate" in kwargs:
        raise ValueError("pass fee/slippage only through base rates and multipliers")
    rows = list(candles)
    if not rows:
        raise ValueError("candles must not be empty")
    funding = kwargs.get("funding_rates")
    if funding is not None:
        kwargs["funding_rates"] = list(funding)

    definitions = (
        ("base", 1.0),
        ("stress", stress),
        ("severe_stress", severe),
    )
    scenarios = {}
    for name, multiplier in definitions:
        fee = base_fee * multiplier
        slippage = base_slippage * multiplier
        if fee >= 1 or slippage >= 1:
            raise ValueError(
                f"{name} costs must remain below 1; got fee_rate={fee}, slippage_rate={slippage}"
            )
        result = run_ema_cross_backtest(
            rows, fee_rate=fee, slippage_rate=slippage,
            trade_start_ts=trade_start_ts, **kwargs,
        )
        scenarios[name] = _metrics(result, fee, slippage)

    return {
        "status": "FULL_BACKTEST_RERUNS",
        "evaluation_window": {
            "start_ts": trade_start_ts,
            "first_candle_ts": rows[0].get("t"),
            "last_candle_ts": rows[-1].get("t"),
            "bars": len(rows),
        },
        "scenario_assumptions": {
            "stress_multiplier": stress,
            "severe_stress_multiplier": severe,
            "changed_dimensions": ["fee_rate", "slippage_rate"],
            "held_constant": ["candles", "strategy parameters", "trade_start_ts", "funding events"],
            "venue_cost_calibration_verified": False,
        },
        "scenarios": scenarios,
        "limitations": [
            "Each scenario is a fresh simulator run; metrics are not estimated by scaling prior trade PnL.",
            "Stress multipliers are user-configurable assumptions, not exchange-specific measured costs.",
            "Funding rates and events are held constant; this is not a funding-rate stress test.",
            "OHLC candles cannot establish intrabar ordering beyond the simulator's conservative rules.",
            "Research output only; results do not establish future profitability or live execution quality.",
        ],
    }
