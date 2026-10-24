"""Chronological holdout, forward-fold, and cost-stress evaluation for V12."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from research.v12_backtest import load_feature_csv, run_v12_backtest, MODEL_VERSION


def run_v12_validation(rows, symbol, *, holdout_fraction=0.2, folds=4,
                       fee_rate=0.0005, slippage_rate=0.0002,
                       initial_equity=1.0, risk_fraction=0.005, max_leverage=3.0):
    if not math.isfinite(holdout_fraction) or not 0.1 <= holdout_fraction <= 0.4:
        raise ValueError("holdout_fraction must be between 0.1 and 0.4")
    if not isinstance(folds, int) or isinstance(folds, bool) or folds < 2:
        raise ValueError("folds must be an integer >= 2")
    if len(rows) < max(30, folds * 5):
        raise ValueError("not enough feature rows for holdout and forward evaluation")

    split = int(len(rows) * (1 - holdout_fraction))
    if split < 10 or len(rows) - split < 5:
        raise ValueError("insufficient rows in chronological holdout split")
    common = {
        "fee_rate": fee_rate, "slippage_rate": slippage_rate,
        "initial_equity": initial_equity, "risk_fraction": risk_fraction,
        "max_leverage": max_leverage,
    }
    full = run_v12_backtest(rows, symbol, **common)
    holdout_rows = rows[split:]
    holdout = run_v12_backtest(holdout_rows, symbol, **common)

    # Frozen parameters: these are contiguous forward evaluation windows, not retraining.
    wf_start = max(int(len(rows) * 0.5), 1)
    available = len(rows) - wf_start
    if available < folds * 3:
        raise ValueError("not enough rows for requested forward folds")
    walk_forward = []
    for fold in range(folds):
        start = wf_start + (available * fold) // folds
        end = wf_start + (available * (fold + 1)) // folds
        if end - start < 3:
            raise ValueError("forward fold has fewer than three rows")
        result = run_v12_backtest(rows[start:end], symbol, **common)
        walk_forward.append({
            "fold": fold + 1, "start_ts": rows[start]["t"], "end_ts": rows[end - 1]["t"],
            "bars": end - start,
            "metrics": {key: result[key] for key in (
                "trades", "wins", "losses", "win_rate_pct", "profit_factor",
                "net_return_pct", "max_drawdown_pct", "equity_final",
            )},
        })

    cost_stress = {}
    for name, multiplier in (("base", 1.0), ("stress_1_5x", 1.5), ("severe_2x", 2.0)):
        result = run_v12_backtest(
            holdout_rows, symbol, fee_rate=fee_rate * multiplier,
            slippage_rate=slippage_rate * multiplier, initial_equity=initial_equity,
            risk_fraction=risk_fraction, max_leverage=max_leverage,
        )
        cost_stress[name] = {key: result[key] for key in (
            "trades", "win_rate_pct", "profit_factor", "net_return_pct",
            "max_drawdown_pct", "equity_final",
        )}

    return {
        "model_version": MODEL_VERSION, "symbol": symbol.upper(),
        "status": "RESEARCH_ONLY", "profitability_claim_eligible": False,
        "strategy_approval": False, "live_trading_enabled": False,
        "dataset": {"rows": len(rows), "first_ts": rows[0]["t"], "last_ts": rows[-1]["t"]},
        "methodology": {
            "holdout_fraction": holdout_fraction, "holdout_start_index": split,
            "forward_folds": folds, "forward_start_index": wf_start,
            "parameter_policy": "frozen; no tuning on holdout",
            "note": "Forward folds are evaluation windows, not retraining; features must be causally generated.",
        },
        "full_sample": {key: full[key] for key in (
            "trades", "win_rate_pct", "profit_factor", "net_return_pct", "max_drawdown_pct",
        )},
        "holdout": {key: holdout[key] for key in (
            "trades", "win_rate_pct", "profit_factor", "net_return_pct",
            "max_drawdown_pct", "equity_final", "trade_log",
        )},
        "forward_folds": walk_forward,
        "holdout_cost_stress": cost_stress,
        "limitations": [
            "Funding cash flows are not included in PnL.",
            "OI, CVD and spread are unavailable unless independently aligned data is added.",
            "No empirical conclusion is valid until real historical data is loaded and the report is reviewed.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, help="causally precomputed V12 feature CSV")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--fee-rate", type=float, default=0.0005)
    parser.add_argument("--slippage-rate", type=float, default=0.0002)
    parser.add_argument("--initial-equity", type=float, default=1.0)
    parser.add_argument("--risk-fraction", type=float, default=0.005)
    parser.add_argument("--max-leverage", type=float, default=3.0)
    parser.add_argument("--holdout-fraction", type=float, default=0.2)
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    source = Path(args.csv).resolve()
    output = Path(args.output).resolve()
    if source == output:
        raise ValueError("output must not overwrite input CSV")
    rows = load_feature_csv(str(source))
    report = run_v12_validation(
        rows, args.symbol, holdout_fraction=args.holdout_fraction, folds=args.folds,
        fee_rate=args.fee_rate, slippage_rate=args.slippage_rate,
        initial_equity=args.initial_equity, risk_fraction=args.risk_fraction,
        max_leverage=args.max_leverage,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
