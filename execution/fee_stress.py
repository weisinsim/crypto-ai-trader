"""Fee-rate sensitivity analysis for multi-scenario paper replays.

This is a research helper, not a prediction of actual exchange fees. Each fee
case reruns the same caller-supplied candles and quantities with a different
entry/exit fee rate. Funding and slippage are not modeled by this helper.
"""
from __future__ import annotations

import math

from execution.multi_candle_replay import replay_multiple_events


def run_fee_stress(scenarios, *, fee_rates=(0.0004, 0.0005, 0.0010),
                   starting_equity=1000.0):
    """Run identical scenarios across validated fee-rate assumptions."""
    if not isinstance(fee_rates, (list, tuple)) or not fee_rates:
        raise ValueError("fee_rates_must_be_nonempty_sequence")
    validated = []
    for rate in fee_rates:
        if (not isinstance(rate, (int, float)) or isinstance(rate, bool)
                or not math.isfinite(float(rate)) or rate < 0 or rate >= 0.1):
            raise ValueError("invalid_fee_rate")
        validated.append(float(rate))
    if len(set(validated)) != len(validated):
        raise ValueError("duplicate_fee_rate")

    cases = []
    for rate in validated:
        case_scenarios = []
        for scenario in scenarios:
            if not isinstance(scenario, dict):
                raise ValueError("invalid_scenario")
            copied = dict(scenario)
            copied["fee_rate"] = rate
            case_scenarios.append(copied)
        replay = replay_multiple_events(
            case_scenarios, starting_equity=starting_equity
        )
        metrics = replay["closed_trade_metrics"]
        cases.append({
            "fee_rate": rate,
            "fee_rate_bps": rate * 10000.0,
            "net_pnl": metrics["net_pnl"],
            "ending_equity": metrics["ending_equity"],
            "max_drawdown_pct": metrics["max_drawdown_pct"],
            "trade_count": metrics["trade_count"],
            "open_trade_count": replay["open_trade_count"],
            "fees_paid": metrics["fees_paid"],
            "funding_paid": metrics["funding_paid"],
        })
    return {
        "cases": cases,
        "scenario_count": len(scenarios),
        "research_only": True,
        "orders_sent": 0,
        "slippage_modeled": False,
        "funding_stress_modeled": False,
    }
