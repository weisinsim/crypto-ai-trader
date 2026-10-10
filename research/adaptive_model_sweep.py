"""Train-only parameter selection with an untouched chronological holdout."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean, median

from research.backtest import run_ema_cross_backtest
from research.run_backtest import load_csv, max_drawdown_pct


# Small, pre-declared grid limits the search space and discourages overfitting.
GRID = [
    {"fast": fast, "slow": slow, "stop_atr": stop, "target_atr": target}
    for fast, slow in ((10, 30), (15, 40), (20, 50), (30, 80))
    for stop in (1.5, 2.0)
    for target in (1.5, 2.0, 2.5)
]


def summarize(result: dict, start_ts: int, end_ts: int) -> dict:
    trades = [t for t in result["trade_log"] if start_ts <= t["entry_time"] <= end_ts]
    curve = [p for p in result["equity_curve"] if start_ts <= p["t"] <= end_ts]
    wins = sum(t["pnl_equity"] > 0 for t in trades)
    gross_profit = sum(t["pnl_equity"] for t in trades if t["pnl_equity"] > 0)
    gross_loss = -sum(t["pnl_equity"] for t in trades if t["pnl_equity"] < 0)
    return {
        "trades": len(trades),
        "net_return_pct": round((result["equity_final"] - 1.0) * 100, 4),
        "max_drawdown_pct": max_drawdown_pct(curve),
        "win_rate_pct": round(wins / len(trades) * 100, 2) if trades else None,
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else (None if not gross_profit else "INF"),
        "expectancy_equity_pct": round(mean(t["pnl_equity"] for t in trades) * 100, 4) if trades else None,
    }


def evaluate_window(rows: list[dict], start: int, end: int, params: dict,
                    fee_rate: float, slippage_rate: float) -> dict:
    # Prefix history warms up indicators; trade_start_ts gates entries to this window.
    result = run_ema_cross_backtest(
        rows[:end], trade_start_ts=rows[start]["t"],
        fee_rate=fee_rate, slippage_rate=slippage_rate, risk_fraction=0.01,
        **params,
    )
    return summarize(result, rows[start]["t"], rows[end - 1]["t"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--interval", choices=("1h", "4h"), default="1h")
    parser.add_argument("--train-fraction", type=float, default=0.6)
    parser.add_argument("--fee-rate", type=float, default=0.0005)
    parser.add_argument("--slippage-rate", type=float, default=0.0002)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    interval_ms = {"1h": 3_600_000, "4h": 14_400_000}[args.interval]
    rows = load_csv(args.csv, interval_ms)
    if not 0.3 <= args.train_fraction <= 0.8:
        raise ValueError("train-fraction must be between 0.3 and 0.8")
    if not rows or len(rows) < 500:
        raise ValueError("adaptive sweep requires at least 500 contiguous candles")
    split = int(len(rows) * args.train_fraction)
    if split < 300 or len(rows) - split < 100:
        raise ValueError("insufficient train or holdout bars")

    # Three contiguous forward folds entirely inside training data.
    train_eval_start = max(200, split // 3)
    width = (split - train_eval_start) // 3
    if width < 50:
        raise ValueError("insufficient bars for three training validation folds")
    windows = [(train_eval_start + i * width, split if i == 2 else train_eval_start + (i + 1) * width)
               for i in range(3)]
    candidates = []
    for params in GRID:
        folds = [evaluate_window(rows, a, b, params, args.fee_rate, args.slippage_rate)
                 for a, b in windows]
        # Score robustly: median fold return penalized by median drawdown.
        # Candidates with too few trades or a losing median fold are not deployable.
        median_return = median(x["net_return_pct"] for x in folds)
        median_dd = median(x["max_drawdown_pct"] for x in folds)
        total_trades = sum(x["trades"] for x in folds)
        score = round(median_return - 0.5 * median_dd, 4)
        eligible = total_trades >= 12 and median_return > 0 and all(x["trades"] >= 2 for x in folds)
        candidates.append({
            "parameters": params, "training_folds": folds, "total_training_trades": total_trades,
            "median_fold_return_pct": round(median_return, 4),
            "median_fold_drawdown_pct": round(median_dd, 4),
            "selection_score": score, "eligible": eligible,
        })

    # Choose only among eligible candidates; if none qualifies, explicitly return NO_CANDIDATE.
    eligible_candidates = [x for x in candidates if x["eligible"]]
    eligible_candidates.sort(key=lambda x: (x["selection_score"], x["median_fold_return_pct"]), reverse=True)
    selected = eligible_candidates[0] if eligible_candidates else None
    boundary = rows[split]["t"]
    holdout = None
    if selected:
        holdout_result = run_ema_cross_backtest(
            rows, trade_start_ts=boundary, fee_rate=args.fee_rate,
            slippage_rate=args.slippage_rate, risk_fraction=0.01,
            **selected["parameters"],
        )
        holdout = summarize(holdout_result, boundary, rows[-1]["t"])

    report = {
        "status": "RESEARCH_ONLY",
        "symbol": args.symbol.upper(), "interval": args.interval,
        "bars": len(rows), "first_ts": rows[0]["t"], "last_ts": rows[-1]["t"],
        "split": {"train_fraction": args.train_fraction, "train_bars": split,
                  "holdout_bars": len(rows) - split, "holdout_start_ts": boundary},
        "costs": {"fee_rate_per_fill": args.fee_rate, "slippage_rate_per_fill": args.slippage_rate,
                  "funding": "NOT INCLUDED: historical funding must be added before any deployment decision"},
        "selection_method": "24 predeclared parameter sets; three chronological folds within training data only; median fold return minus 0.5x median drawdown; requires >=12 trades, >=2 trades/fold and positive median fold return",
        "candidate_count": len(candidates), "eligible_count": len(eligible_candidates),
        "selected_parameters": selected["parameters"] if selected else None,
        "training_selection_metrics": ({k: v for k, v in selected.items() if k != "training_folds"}
                                       if selected else None),
        "holdout": holdout,
        "all_candidates": sorted(candidates, key=lambda x: x["selection_score"], reverse=True),
        "warnings": [
            "No candidate is not a failure of the runner; it means training evidence was insufficient.",
            "Holdout is evaluated only after selecting parameters from training data.",
            "This is a long/short EMA-cross research baseline, not a production or profitability certification.",
            "No historical funding data is included; do not use these results to justify live trading.",
        ],
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"status={report['status']} symbol={args.symbol.upper()} candidates={len(candidates)} "
          f"eligible={len(eligible_candidates)} selected={bool(selected)} output={output}")


if __name__ == "__main__":
    main()
