"""Funding sensitivity summaries for frozen research backtests.

This summarizes completed runs; it does not rerun strategies or authenticate
funding data. Scenario comparisons require identical inputs except for the
intended stress dimension.
"""
from __future__ import annotations

from math import isfinite
from typing import Mapping


def _finite_number(value: object, field: str, *, allow_none: bool = False) -> float | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number")
    try:
        if not isfinite(value):
            raise ValueError(f"{field} must be a finite number")
        converted = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite number") from exc
    if not isfinite(converted):
        raise ValueError(f"{field} must be a finite number")
    return converted


def summarize_funding_scenarios(scenarios: Mapping[str, Mapping[str, object]]) -> dict:
    """Summarize performance and funding-ledger quality for named backtest results."""
    if not isinstance(scenarios, Mapping) or not scenarios:
        raise ValueError("scenarios must be a non-empty mapping")
    summaries = []
    for name, result in scenarios.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("scenario names must be non-empty strings")
        if not isinstance(result, Mapping):
            raise ValueError(f"scenario {name!r} result must be a mapping")
        status = result.get("status")
        if status not in ("COMPLETED", "INSUFFICIENT_DATA"):
            raise ValueError(f"scenario {name!r} has unsupported status")
        trade_log = result.get("trade_log", [])
        if not isinstance(trade_log, list):
            raise ValueError(f"scenario {name!r} trade_log must be a list")
        cashflow_total = 0.0
        settled_trades = 0
        settlement_total = 0
        ambiguous_trades = 0
        for index, trade in enumerate(trade_log):
            if not isinstance(trade, Mapping):
                raise ValueError(f"scenario {name!r} trade {index} must be a mapping")
            cashflow = _finite_number(
                trade.get("funding_cashflow", 0.0),
                f"scenario {name!r} trade {index} funding_cashflow",
            )
            cashflow_total += cashflow
            if not isfinite(cashflow_total):
                raise ValueError(f"scenario {name!r} cumulative funding cashflow must remain finite")
            count = trade.get("funding_settlement_count", 0)
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError(f"scenario {name!r} trade {index} funding_settlement_count must be a non-negative integer")
            ambiguous = trade.get("funding_boundary_ambiguous", False)
            if not isinstance(ambiguous, bool):
                raise ValueError(f"scenario {name!r} trade {index} funding_boundary_ambiguous must be boolean")
            settled_trades += int(count > 0)
            settlement_total += count
            ambiguous_trades += int(ambiguous)

        trades = result.get("trades", len(trade_log))
        if isinstance(trades, bool) or not isinstance(trades, int) or trades < 0:
            raise ValueError(f"scenario {name!r} trades must be a non-negative integer")
        if trades != len(trade_log):
            raise ValueError(f"scenario {name!r} trades does not match trade_log length")
        pf = result.get("profit_factor")
        if pf != "INF" and pf is not None:
            pf = _finite_number(pf, f"{name}.profit_factor")
        summaries.append({
            "scenario": name,
            "status": status,
            "trades": trades,
            "net_return_pct": _finite_number(result.get("net_return_pct"), f"{name}.net_return_pct", allow_none=True),
            "max_drawdown_pct": _finite_number(result.get("max_drawdown_pct"), f"{name}.max_drawdown_pct", allow_none=True),
            "profit_factor": pf,
            "funding_cashflow_total": round(cashflow_total, 10),
            "trades_with_funding_settlements": settled_trades,
            "funding_settlement_count_total": settlement_total,
            "ambiguous_boundary_trades": ambiguous_trades,
            "ambiguous_boundary_trade_pct": round(ambiguous_trades / trades * 100, 4) if trades else None,
            "equity_floor_hit": result.get("equity_floor_hit"),
        })
    return {
        "status": "SUMMARY_ONLY_NOT_A_BACKTEST",
        "scenario_count": len(summaries),
        "scenarios": summaries,
        "limitations": [
            "Scenario metrics are comparable only when candles, frozen parameters, execution assumptions, and funding-event inputs are held constant except for the intended stress dimension.",
            "Funding cashflow totals are ledger summaries; they do not recompute equity, compounding, or drawdown.",
            "A flagged funding/stop boundary is ambiguous in OHLC data; this report counts it but does not infer intrabar event ordering.",
            "Funding source authenticity and event completeness must be validated separately.",
        ],
    }
