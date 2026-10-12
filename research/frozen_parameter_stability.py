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


def summarize_monthly_trades(trades: list[dict]) -> dict:
    """Summarize realized holdout trades by UTC entry month without retuning parameters."""
    from datetime import datetime, timezone

    grouped: dict[str, list[dict]] = {}
    for trade in trades:
        month = datetime.fromtimestamp(int(trade["entry_time"]) / 1000, timezone.utc).strftime("%Y-%m")
        grouped.setdefault(month, []).append(trade)

    result = {}
    for month, items in sorted(grouped.items()):
        pnls = [float(item["pnl_equity"]) for item in items]
        wins = [pnl for pnl in pnls if pnl > 0]
        losses = [-pnl for pnl in pnls if pnl < 0]
        gross_loss = sum(losses)
        result[month] = {
            "trades": len(items),
            "net_pnl_equity_pct": round(sum(pnls) * 100, 4),
            "win_rate_pct": round(len(wins) / len(items) * 100, 2),
            "profit_factor": round(sum(wins) / gross_loss, 4) if gross_loss else ("INF" if wins else None),
            "long_trades": sum(item["side"] == "LONG" for item in items),
            "short_trades": sum(item["side"] == "SHORT" for item in items),
            "exit_reasons": {reason: sum(item["reason"] == reason for item in items)
                             for reason in sorted({item["reason"] for item in items})},
        }
    return result


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
    monthly_trade_diagnostics = {}
    # Descriptive direction sensitivity on the untouched holdout; never used to select parameters.
    direction_sensitivity = {}
    for direction in ("LONG", "SHORT", "BOTH"):
        direction_params = dict(frozen_params)
        direction_params["direction"] = direction
        direction_sensitivity[direction] = {}
        for cost_name, cost in COSTS.items():
            metrics = simulate(rows, direction_params, split, len(rows),
                               fee=cost["fee"], slippage=cost["slippage"],
                               funding_rates=funding_rates, symbol=symbol)
            metrics.pop("trade_log", None)
            direction_sensitivity[direction][cost_name] = metrics
    for segment, (start, end) in ranges.items():
        results[segment] = {}
        for cost_name, cost in COSTS.items():
            metrics = simulate(rows, frozen_params, start, end,
                               fee=cost["fee"], slippage=cost["slippage"],
                               funding_rates=funding_rates, symbol=symbol)
            if segment == "holdout_full":
                monthly_trade_diagnostics[cost_name] = summarize_monthly_trades(metrics["trade_log"])
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
        "monthly_trade_diagnostics": monthly_trade_diagnostics,
        "direction_sensitivity_same_holdout_not_for_selection": direction_sensitivity,
        "warnings": [
            "Direction sensitivity replays LONG, SHORT, and BOTH on the same holdout for diagnosis only; it is not independent validation and must not be used to choose a direction based on holdout performance.",
            "Monthly diagnostics are descriptive slices of the same holdout, not independent validation or a basis for retuning against the holdout.",
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
