"""AVAX frozen-parameter diagnostics; never select parameters from holdout results."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from research.mean_reversion import simulate
from research.run_backtest import load_csv, load_funding_csv


def metrics(trades: list[dict]) -> dict:
    pnl = [float(t["pnl_equity"]) for t in trades]
    gains = sum(x for x in pnl if x > 0)
    losses = -sum(x for x in pnl if x < 0)
    return {
        "trades": len(pnl),
        "net_trade_pnl_equity_pct": round(sum(pnl) * 100, 4),
        "win_rate_pct": round(sum(x > 0 for x in pnl) / len(pnl) * 100, 2) if pnl else None,
        "profit_factor": round(gains / losses, 4) if losses else ("INF" if gains else None),
    }


def concentration(trades: list[dict]) -> dict:
    pnl = [float(t["pnl_equity"]) for t in trades]
    winners = sorted((x for x in pnl if x > 0), reverse=True)
    positive_total = sum(winners)
    result = {
        "positive_pnl_trade_concentration": {
            f"top_{n}_share_pct": round(sum(winners[:n]) / positive_total * 100, 2)
            if positive_total else None for n in (1, 3, 5)
        }
    }
    for n in (1, 3):
        removed_idx = set(
            sorted(
                (i for i, value in enumerate(pnl) if value > 0),
                key=lambda i: pnl[i],
                reverse=True,
            )[:n]
        )
        # Additive trade-PnL diagnostic only; this is not a portfolio re-simulation.
        result[f"after_removing_top_{n}_winning_trades_additive_only"] = metrics(
            [trade for i, trade in enumerate(trades) if i not in removed_idx]
        )
    return result



def trade_attribution(trades: list[dict]) -> dict:
    """Describe realized trades by entry month, side, and exit reason; never retune."""
    from datetime import datetime, timezone

    groups: dict[str, list[dict]] = {}
    for trade in trades:
        month = datetime.fromtimestamp(int(trade["entry_time"]) / 1000, timezone.utc).strftime("%Y-%m")
        for key in (f"month:{month}", f"side:{trade['side']}", f"exit_reason:{trade['reason']}"):
            groups.setdefault(key, []).append(trade)

    summary = {}
    for key, items in sorted(groups.items()):
        pnls = [float(item["pnl_equity"]) for item in items]
        durations = [max(0.0, (int(item["exit_time"]) - int(item["entry_time"])) / 3_600_000)
                     for item in items]
        summary[key] = {
            "trades": len(items),
            "net_trade_pnl_equity_pct_additive": round(sum(pnls) * 100, 4),
            "win_rate_pct": round(sum(value > 0 for value in pnls) / len(pnls) * 100, 2),
            "mean_hold_hours": round(sum(durations) / len(durations), 2),
            "stop_count": sum(item["reason"] == "STOP" for item in items),
            "price_pnl_equity_pct_additive": round(sum(float(item.get("price_pnl_equity", 0)) for item in items) * 100, 4),
            "trading_cost_equity_pct_additive": round(sum(float(item.get("trading_cost_equity", 0)) for item in items) * 100, 4),
            "funding_pnl_equity_pct_additive": round(sum(float(item.get("funding_pnl_equity", 0)) for item in items) * 100, 4),
        }
    return summary


def run(rows, params, start, end, cost, funding, symbol):
    # Existing simulator defines cost as fee + slippage per side, so these values
    # reproduce the workflow's base, 0.30%, and 0.46% round-trip scenarios.
    result = simulate(rows, params, start, end, fee=0.0005, slippage=cost,
                      funding_rates=funding, symbol=symbol)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--csv", required=True)
    p.add_argument("--funding-csv", required=True)
    p.add_argument("--symbol", default="AVAXUSDT")
    p.add_argument("--params-json", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--train-fraction", type=float, default=0.6)
    args = p.parse_args()
    if not 0.3 <= args.train_fraction <= 0.8:
        raise ValueError("train-fraction must be between 0.3 and 0.8")
    rows = load_csv(args.csv, 3_600_000)
    funding = load_funding_csv(args.funding_csv)
    if {e["symbol"] for e in funding} != {args.symbol.upper()}:
        raise ValueError("funding symbol mismatch")
    funding = [e for e in funding if rows[0]["t"] <= e["funding_time"] <= rows[-1]["t"]]
    if not funding:
        raise ValueError("funding CSV has no settlement events inside candle data range")
    report = json.loads(Path(args.params_json).read_text(encoding="utf-8"))
    if report.get("symbol", "").upper() != args.symbol.upper():
        raise ValueError("parameter report symbol does not match requested symbol")
    params = report.get("selected_parameters")
    if not isinstance(params, dict):
        raise ValueError("report must contain selected_parameters; diagnostic-only candidates are not accepted")
    split = int(len(rows) * args.train_fraction)
    costs = {"base": 0.0002, "stress_0_30pct_round_trip": 0.0010,
             "severe_0_46pct_round_trip": 0.0018}
    # Parameter-neighborhood diagnostics are restricted to training bars.
    # Exclude all post-training funding events to preserve causal data boundaries.
    training_funding = [event for event in funding if event["funding_time"] <= rows[split - 1]["t"]]
    if not training_funding:
        raise ValueError("no funding settlements available inside the training window")
    neighbors = []
    variants = [
        ("window_minus_12", {"window": max(24, params["window"] - 12)}),
        ("window_plus_12", {"window": min(72, params["window"] + 12)}),
        ("entry_z_minus_0_25", {"entry_z": max(1.5, params["entry_z"] - 0.25)}),
        ("entry_z_plus_0_25", {"entry_z": min(2.0, params["entry_z"] + 0.25)}),
        ("stop_atr_minus_0_25", {"stop_atr": max(1.5, params["stop_atr"] - 0.25)}),
        ("stop_atr_plus_0_25", {"stop_atr": min(2.0, params["stop_atr"] + 0.25)}),
    ]
    for name, changes in variants:
        candidate = dict(params)
        candidate.update(changes)
        for cost_name, slippage in costs.items():
            r = run(rows[:split], candidate, max(100, split // 3), split, slippage, training_funding, args.symbol)
            neighbors.append({"variant": name, "parameters": candidate, "cost_scenario": cost_name,
                              "metrics": {k: v for k, v in r.items() if k != "trade_log"}})
    halves = [(split, split + (len(rows)-split)//2), (split + (len(rows)-split)//2, len(rows))]
    holdout_results = []
    for idx, (a, b) in enumerate(halves, start=1):
        for cost_name, slippage in costs.items():
            r = run(rows, params, a, b, slippage, funding, args.symbol)
            holdout_results.append({"half": idx, "cost_scenario": cost_name,
                                    "metrics": {k: v for k, v in r.items() if k != "trade_log"},
                                    "trade_pnl_concentration": concentration(r["trade_log"]),
                                    "trade_attribution_additive_only": trade_attribution(r["trade_log"])})
    output = {
        "status": "RESEARCH_ONLY",
        "symbol": args.symbol.upper(),
        "parameters_frozen_from_training_report": params,
        "split": {"train_bars": split, "holdout_bars": len(rows)-split},
        "train_only_parameter_neighborhood": neighbors,
        "chronological_holdout_halves": holdout_results,
        "limitations": [
            "Parameter neighbors are evaluated on training data only and are diagnostics, not a new selection pass.",
            "Removing winners is an additive trade-PnL diagnostic, not a compounded equity re-simulation.",
            "The existing simulator compounds position PnL; per-trade contribution diagnostics should not be equated to account-return counterfactuals.",
            "Funding mark prices and settlement coverage still require exchange-source reconciliation.",
            "No result in this report is production approval."
        ],
        "production_gate": "NOT_APPROVED_BY_THIS_DIAGNOSTIC",
    }
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"status=RESEARCH_ONLY symbol={args.symbol} output={args.output} holdout_halves={len(holdout_results)} neighborhood_rows={len(neighbors)}")


if __name__ == "__main__":
    main()
