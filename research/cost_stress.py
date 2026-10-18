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
    if not isinstance(result, Mapping):
        raise ValueError("backtest result must be a mapping")
    trade_log = result.get("trade_log", [])
    if not isinstance(trade_log, list):
        raise ValueError("backtest trade_log must be a list")
    funding_cashflow_total = 0.0
    settlement_count_total = 0
    for index, trade in enumerate(trade_log):
        if not isinstance(trade, Mapping):
            raise ValueError(f"backtest trade_log item {index} must be a mapping")
        raw_cashflow = trade.get("funding_cashflow", 0.0)
        if isinstance(raw_cashflow, bool):
            raise ValueError(f"trade {index} funding_cashflow must be finite")
        try:
            cashflow = float(raw_cashflow)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"trade {index} funding_cashflow must be finite") from exc
        if not math.isfinite(cashflow):
            raise ValueError(f"trade {index} funding_cashflow must be finite")
        funding_cashflow_total += cashflow
        if not math.isfinite(funding_cashflow_total):
            raise ValueError("cumulative funding cashflow must remain finite")
        raw_count = trade.get("funding_settlement_count", 0)
        if isinstance(raw_count, bool) or not isinstance(raw_count, int) or raw_count < 0:
            raise ValueError(f"trade {index} funding_settlement_count must be a non-negative integer")
        settlement_count_total += raw_count

    for metric_name in ("net_return_pct", "max_drawdown_pct", "profit_factor", "equity_final"):
        metric_value = result.get(metric_name)
        valid_infinite_profit_factor = metric_name == "profit_factor" and metric_value == "INF"
        valid_positive_infinity_pf = (
            metric_name == "profit_factor"
            and isinstance(metric_value, (int, float))
            and not isinstance(metric_value, bool)
            and metric_value == math.inf
        )
        if metric_value is not None and not valid_infinite_profit_factor and not valid_positive_infinity_pf and (
            isinstance(metric_value, bool)
            or not isinstance(metric_value, (int, float))
            or not math.isfinite(metric_value)
        ):
            raise ValueError(f"backtest {metric_name} must be a valid number or None")
    if not isinstance(result.get("equity_floor_hit", False), bool):
        raise ValueError("backtest equity_floor_hit must be a boolean")

    return {
        "status": result.get("status", "UNKNOWN"),
        "fee_rate": fee_rate,
        "slippage_rate": slippage_rate,
        "trades": len(trade_log),
        "net_return_pct": result.get("net_return_pct"),
        "max_drawdown_pct": result.get("max_drawdown_pct"),
        "profit_factor": result.get("profit_factor"),
        "equity_final": result.get("equity_final"),
        "equity_floor_hit": result.get("equity_floor_hit", False),
        "funding_cashflow_total": round(funding_cashflow_total, 10),
        "funding_settlement_count_total": settlement_count_total,
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

    base_metrics = scenarios["base"]
    comparisons = {}
    for name in ("stress", "severe_stress"):
        stressed = scenarios[name]
        comparisons[name] = {
            "net_return_change_vs_base_pct_points": (
                round(stressed["net_return_pct"] - base_metrics["net_return_pct"], 4)
                if isinstance(stressed.get("net_return_pct"), (int, float))
                and isinstance(base_metrics.get("net_return_pct"), (int, float))
                and math.isfinite(stressed["net_return_pct"])
                and math.isfinite(base_metrics["net_return_pct"]) else None
            ),
            "max_drawdown_change_vs_base_pct_points": (
                round(stressed["max_drawdown_pct"] - base_metrics["max_drawdown_pct"], 4)
                if isinstance(stressed.get("max_drawdown_pct"), (int, float))
                and isinstance(base_metrics.get("max_drawdown_pct"), (int, float))
                and math.isfinite(stressed["max_drawdown_pct"])
                and math.isfinite(base_metrics["max_drawdown_pct"]) else None
            ),
            "profit_factor_change_vs_base": (
                round(stressed["profit_factor"] - base_metrics["profit_factor"], 4)
                if isinstance(stressed.get("profit_factor"), (int, float))
                and isinstance(base_metrics.get("profit_factor"), (int, float))
                and math.isfinite(stressed["profit_factor"])
                and math.isfinite(base_metrics["profit_factor"]) else None
            ),
        }
    # This is a conservative diagnostic, not an approval gate: the EMA baseline
    # runner is a research tool and does not establish live execution parity.
    robustness_screen = {
        "criteria": "all three reruns completed, no equity floor hit, net return > 0, PF > 1.15",
        "pass": all(
            item.get("status") == "COMPLETED"
            and item.get("equity_floor_hit") is False
            and isinstance(item.get("net_return_pct"), (int, float))
            and math.isfinite(item["net_return_pct"]) and item["net_return_pct"] > 0
            and (
                item.get("profit_factor") == "INF"
                or (
                    isinstance(item.get("profit_factor"), (int, float))
                    and not isinstance(item.get("profit_factor"), bool)
                    and math.isfinite(item["profit_factor"])
                    and item["profit_factor"] > 1.15
                )
            )
            for item in scenarios.values()
        ),
        "production_approval": False,
    }
    return {
        "status": "FULL_BACKTEST_RERUNS",
        "evaluation_window": {
            "start_ts": trade_start_ts,
            "first_candle_ts": rows[0].get("t"),
            "last_candle_ts": rows[-1].get("t"),
            "bars": len(rows),
        },
        "comparisons_vs_base": comparisons,
        "robustness_screen": robustness_screen,
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
