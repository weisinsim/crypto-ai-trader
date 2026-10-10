"""Evaluate frozen ETH mean-reversion parameters across symbols, holdout subperiods, and cost assumptions.

Research-only diagnostic. Parameters are frozen and never optimized in this script.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from research.run_backtest import load_csv
from research.mean_reversion import simulate

FROZEN_PARAMS = {
    "window": 72,
    "entry_z": 2.0,
    "stop_atr": 1.5,
    "rsi_filter": 0,
    "direction": "SHORT",
}
COSTS = {
    "base": {"fee": 0.0005, "slippage": 0.0002},
    "stress_0_30pct_round_trip": {"fee": 0.0005, "slippage": 0.001},
    "severe_0_46pct_round_trip": {"fee": 0.0005, "slippage": 0.0018},
}


def evaluate(rows: list[dict], train_fraction: float) -> dict:
    split = int(len(rows) * train_fraction)
    if split < 200 or len(rows) - split < 100:
        raise ValueError("insufficient rows for frozen-parameter holdout analysis")
    holdout_n = len(rows) - split
    mid = split + holdout_n // 2
    ranges = {
        "holdout_first_half": (split, mid),
        "holdout_second_half": (mid, len(rows)),
        "holdout_full": (split, len(rows)),
    }
    results = {}
    for segment, (start, end) in ranges.items():
        results[segment] = {}
        for cost_name, cost in COSTS.items():
            metrics = simulate(rows, FROZEN_PARAMS, start, end,
                               fee=cost["fee"], slippage=cost["slippage"])
            metrics.pop("trade_log", None)
            results[segment][cost_name] = metrics
    return {
        "status": "RESEARCH_ONLY",
        "parameters_frozen_from_eth_research": FROZEN_PARAMS,
        "bars": len(rows),
        "train_fraction_used_only_to_define_holdout": train_fraction,
        "holdout_bars": holdout_n,
        "cost_assumptions": COSTS,
        "segments": results,
        "warnings": [
            "Cross-symbol runs are transfer diagnostics, not symbol-specific optimized models.",
            "No historical funding-rate data is included in this diagnostic.",
            "Subperiods are slices of the same historical holdout, not independent future data.",
            "All results are hypothetical and do not certify live profitability.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--train-fraction", type=float, default=0.6)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    rows = load_csv(args.csv, 3_600_000)
    report = evaluate(rows, args.train_fraction)
    report["symbol"] = args.symbol.upper()
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"FROZEN PARAMETER STABILITY symbol={args.symbol.upper()} bars={len(rows)}")
    for segment, costs in report["segments"].items():
        for cost_name, metrics in costs.items():
            print(
                f"segment={segment} cost={cost_name} trades={metrics['trades']} "
                f"net_return_pct={metrics['net_return_pct']} "
                f"max_drawdown_pct={metrics['max_drawdown_pct']} "
                f"profit_factor={metrics['profit_factor']}"
            )


if __name__ == "__main__":
    main()
