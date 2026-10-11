"""Evaluate frozen ETH mean-reversion parameters across symbols, holdout subperiods, and cost assumptions.

Research-only diagnostic. Parameters are frozen and never optimized in this script.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from research.run_backtest import load_csv, load_funding_csv
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


def evaluate(rows: list[dict], train_fraction: float, funding_rates: list[dict] | None = None, symbol: str | None = None, params: dict | None = None) -> dict:
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
    if funding_rates is not None:
        if not symbol or any(event.get("symbol", "").upper() != symbol.upper() for event in funding_rates):
            raise ValueError("funding CSV symbol does not match --symbol")
    frozen_params = dict(params or FROZEN_PARAMS)
    required = {"window", "entry_z", "stop_atr", "rsi_filter", "direction"}
    if not required.issubset(frozen_params):
        raise ValueError(f"params must contain {sorted(required)}")
    results = {}
    for segment, (start, end) in ranges.items():
        results[segment] = {}
        for cost_name, cost in COSTS.items():
            metrics = simulate(rows, frozen_params, start, end,
                               fee=cost["fee"], slippage=cost["slippage"],
                               funding_rates=funding_rates, symbol=symbol)
            metrics.pop("trade_log", None)
            results[segment][cost_name] = metrics
    return {
        "status": "RESEARCH_ONLY",
        "parameters_frozen_for_symbol": frozen_params,
        "bars": len(rows),
        "train_fraction_used_only_to_define_holdout": train_fraction,
        "holdout_bars": holdout_n,
        "cost_assumptions": COSTS,
        "funding_data": ({"records": len(funding_rates), "symbol": symbol.upper(),
                          "mark_price_sources": {source: sum(1 for event in funding_rates if event.get("mark_price_source", "unspecified") == source)
                                                 for source in sorted({event.get("mark_price_source", "unspecified") for event in funding_rates})}}
                         if funding_rates is not None else {"records": 0, "source": None}),
        "segments": results,
        "warnings": [
            "Parameters are loaded from the symbol-specific train-only mean-reversion report when --params-json is supplied.",
            "Funding cashflows are included only when --funding-csv is supplied; inspect mark_price_sources for price provenance.",
            "Subperiods are slices of the same historical holdout, not independent future data.",
            "All results are hypothetical and do not certify live profitability.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--train-fraction", type=float, default=0.6)
    parser.add_argument("--funding-csv", help="optional historical funding CSV")
    parser.add_argument("--params-json", help="symbol-specific mean-reversion report; parameters must have been selected on training data only")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    rows = load_csv(args.csv, 3_600_000)
    historical_funding = load_funding_csv(args.funding_csv) if args.funding_csv else None
    if historical_funding is not None:
        if historical_funding[0]["symbol"] != args.symbol.upper():
            raise ValueError("funding CSV symbol does not match --symbol")
        historical_funding = [event for event in historical_funding
                              if rows[0]["t"] <= event["funding_time"] <= rows[-1]["t"]]
        if not historical_funding:
            raise ValueError("funding CSV has no settlement events inside candle data range")
    selected_params = None
    if args.params_json:
        source_report = json.loads(Path(args.params_json).read_text(encoding="utf-8"))
        selected_params = source_report.get("selected_parameters") or source_report.get("selected_params") or source_report.get("parameters") or source_report.get("best_params")
        if not isinstance(selected_params, dict):
            raise ValueError(f"could not find selected_params/parameters/best_params in {args.params_json}")
    report = evaluate(rows, args.train_fraction, historical_funding, args.symbol, selected_params)
    report["symbol"] = args.symbol.upper()
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"FROZEN PARAMETER STABILITY symbol={args.symbol.upper()} bars={len(rows)}")
    print(f"funding_data={report['funding_data']}")
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
