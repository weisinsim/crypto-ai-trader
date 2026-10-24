"""Chronological holdout, train-only threshold calibration, forward-fold, and cost-stress evaluation."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from research.v12_backtest import load_feature_csv, run_v12_backtest, MODEL_VERSION
from research.v12_hybrid_engine import ASSET_PROFILES
from research.run_backtest import load_funding_csv


def _metrics(result):
    return {key: result[key] for key in (
        "trades", "wins", "losses", "win_rate_pct", "profit_factor",
        "net_return_pct", "max_drawdown_pct", "equity_final",
    )}


def run_v12_validation(rows, symbol, *, holdout_fraction=0.2, folds=4,
                       fee_rate=0.0005, slippage_rate=0.0002,
                       initial_equity=1.0, risk_fraction=0.005, max_leverage=3.0,
                       funding_events=None):
    if not math.isfinite(holdout_fraction) or not 0.1 <= holdout_fraction <= 0.4:
        raise ValueError("holdout_fraction must be between 0.1 and 0.4")
    if not isinstance(folds, int) or isinstance(folds, bool) or folds < 2:
        raise ValueError("folds must be an integer >= 2")
    if len(rows) < max(30, folds * 5):
        raise ValueError("not enough feature rows for holdout and forward evaluation")

    symbol = str(symbol).upper()
    split = int(len(rows) * (1 - holdout_fraction))
    if split < 10 or len(rows) - split < 5:
        raise ValueError("insufficient rows in chronological holdout split")
    common = {
        "fee_rate": fee_rate, "slippage_rate": slippage_rate,
        "initial_equity": initial_equity, "risk_fraction": risk_fraction,
        "max_leverage": max_leverage,
        "funding_events": list(funding_events or []),
    }
    full = run_v12_backtest(rows, symbol, **common)
    train_rows = rows[:split]
    holdout_rows = rows[split:]
    baseline_holdout = run_v12_backtest(holdout_rows, symbol, **common)

    # Calibrate only on the training partition. The chronological holdout is never
    # used to select thresholds; if no candidate passes the minimum gates, keep defaults.
    base_score = int(ASSET_PROFILES.get(symbol, {}).get("min_score", 75))
    score_candidates = sorted({base_score, min(base_score + 5, 95),
                               min(base_score + 10, 95), min(base_score + 15, 95)})
    edge_candidates = (8, 12)
    threshold_sweep = []
    eligible = []
    for min_score in score_candidates:
        for min_edge in edge_candidates:
            result = run_v12_backtest(
                train_rows, symbol, **common,
                min_score_override=min_score, min_edge_override=min_edge,
            )
            pf = result["profit_factor"]
            row = {
                "min_score": min_score, "min_edge": min_edge,
                "trades": result["trades"], "win_rate_pct": result["win_rate_pct"],
                "profit_factor": pf, "net_return_pct": result["net_return_pct"],
                "max_drawdown_pct": result["max_drawdown_pct"],
            }
            threshold_sweep.append(row)
            if (
                result["trades"] >= 20
                and isinstance(pf, (int, float)) and math.isfinite(pf) and pf > 1.0
                and result["net_return_pct"] > 0
                and result["max_drawdown_pct"] <= 15
            ):
                eligible.append(row)

    selected = max(
        eligible,
        key=lambda item: (item["net_return_pct"], item["profit_factor"], -item["max_drawdown_pct"]),
        default=None,
    )
    selected_overrides = (
        {"min_score_override": selected["min_score"], "min_edge_override": selected["min_edge"]}
        if selected else {}
    )
    calibrated_holdout = (
        run_v12_backtest(holdout_rows, symbol, **common, **selected_overrides)
        if selected else baseline_holdout
    )

    # Frozen defaults remain visible in the forward folds; the threshold-calibrated
    # holdout is reported separately so the training selection cannot be confused
    # with an independently pre-registered strategy.
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
            "bars": end - start, "metrics": _metrics(result),
        })

    cost_stress = {}
    for name, multiplier in (("base", 1.0), ("stress_1_5x", 1.5), ("severe_2x", 2.0)):
        result = run_v12_backtest(
            holdout_rows, symbol, fee_rate=fee_rate * multiplier,
            slippage_rate=slippage_rate * multiplier, initial_equity=initial_equity,
            risk_fraction=risk_fraction, max_leverage=max_leverage,
            funding_events=list(funding_events or []), **selected_overrides,
        )
        cost_stress[name] = _metrics(result)

    return {
        "model_version": MODEL_VERSION, "symbol": symbol,
        "status": "RESEARCH_ONLY", "profitability_claim_eligible": False,
        "strategy_approval": False, "live_trading_enabled": False,
        "dataset": {"rows": len(rows), "first_ts": rows[0]["t"], "last_ts": rows[-1]["t"]},
        "methodology": {
            "holdout_fraction": holdout_fraction, "holdout_start_index": split,
            "forward_folds": folds, "forward_start_index": wf_start,
            "parameter_policy": "threshold candidates evaluated on training partition only; holdout never used for selection",
            "note": "Forward folds report the frozen default model. Calibrated holdout is reported separately and is not production approval.",
        },
        "full_sample": {key: full[key] for key in (
            "trades", "win_rate_pct", "profit_factor", "funding_pnl_total", "net_return_pct", "max_drawdown_pct",
        )},
        "holdout": {key: baseline_holdout[key] for key in (
            "trades", "win_rate_pct", "profit_factor", "funding_pnl_total", "net_return_pct",
            "max_drawdown_pct", "equity_final", "trade_log",
        )},
        "threshold_calibration": {
            "training_rows": split, "base_min_score": base_score,
            "minimum_training_trades": 20, "eligibility": "PF > 1, positive net return, max drawdown <= 15%",
            "candidates": threshold_sweep,
            "selected": selected,
            "status": "TRAINING_CANDIDATE_SELECTED" if selected else "NO_CANDIDATE_PASSED_TRAINING_GATES",
        },
        "calibrated_holdout": {key: calibrated_holdout[key] for key in (
            "trades", "win_rate_pct", "profit_factor", "funding_pnl_total", "net_return_pct",
            "max_drawdown_pct", "equity_final", "trade_log",
        )},
        "forward_folds": walk_forward,
        "holdout_cost_stress": cost_stress,
        "limitations": [
            "Funding cash flows are included when a validated --funding-csv is supplied; otherwise they are omitted.",
            "OI, CVD and spread are unavailable unless independently aligned data is added.",
            "Forward folds use frozen default thresholds and are not a re-training walk-forward.",
            "A threshold selected on training data still requires an untouched holdout and paper-trading confirmation.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, help="causally precomputed V12 feature CSV")
    parser.add_argument("--funding-csv", help="optional aligned historical funding settlement CSV")
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
    funding_events = load_funding_csv(args.funding_csv) if args.funding_csv else []
    if funding_events and funding_events[0]["symbol"] != args.symbol.upper():
        raise ValueError("funding CSV symbol does not match --symbol")
    report = run_v12_validation(
        rows, args.symbol, holdout_fraction=args.holdout_fraction, folds=args.folds,
        fee_rate=args.fee_rate, slippage_rate=args.slippage_rate,
        initial_equity=args.initial_equity, risk_fraction=args.risk_fraction,
        max_leverage=args.max_leverage, funding_events=funding_events,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
