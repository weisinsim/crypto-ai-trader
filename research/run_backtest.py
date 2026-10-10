"""Run a reproducible EMA baseline over CSV OHLCV with chronological holdout evaluation."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from statistics import mean

from research.backtest import run_ema_cross_backtest, validate_candles


def load_csv(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as handle:
        rows = []
        for row in csv.DictReader(handle):
            rows.append({
                "t": int(row["t"]), "o": float(row["o"]), "h": float(row["h"]),
                "l": float(row["l"]), "c": float(row["c"]),
                "v": float(row.get("v", 0) or 0), "q": float(row.get("q", 0) or 0),
            })
    return validate_candles(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, help="closed-candle CSV from research.binance_data")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--train-fraction", type=float, default=0.6)
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--fast", type=int, default=20)
    parser.add_argument("--slow", type=int, default=50)
    parser.add_argument("--fee-rate", type=float, default=0.0005)
    parser.add_argument("--slippage-rate", type=float, default=0.0002)
    parser.add_argument("--funding-rate-per-bar", type=float, default=0.0)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    rows = load_csv(args.csv)
    if not 0.3 <= args.train_fraction <= 0.8:
        raise ValueError("train-fraction must be between 0.3 and 0.8")
    if args.folds < 2:
        raise ValueError("folds must be >= 2")
    if len(rows) < args.slow * 3:
        raise ValueError("not enough bars for warm-up, holdout and walk-forward")

    common = dict(fast=args.fast, slow=args.slow, fee_rate=args.fee_rate,
                  slippage_rate=args.slippage_rate,
                  funding_rate_per_bar=args.funding_rate_per_bar)
    split = int(len(rows) * args.train_fraction)
    warmup = rows[max(0, split - args.slow * 3):split]
    holdout = rows[split:]
    # Include prior bars for indicator warm-up, but report only trades whose
    # entries occur at or after the holdout boundary.
    boundary_ts = holdout[0]["t"]
    holdout_result = run_ema_cross_backtest(warmup + holdout, trade_start_ts=boundary_ts, **common)
    holdout_trades = holdout_result["trade_log"]
    # Convert fixed-notional trade PnL to the starting-equity basis used by the runner.
    holdout_pnls = [t["pnl_equity"] for t in holdout_trades]
    holdout_profit = sum(p for p in holdout_pnls if p > 0)
    holdout_loss = -sum(p for p in holdout_pnls if p < 0)
    walk_results = []
    n = len(rows)
    # Expanding history windows with non-overlapping forward evaluation windows.
    start_eval = max(split, args.slow * 3)
    eval_width = max(1, (n - start_eval) // args.folds)
    for fold in range(args.folds):
        a = start_eval + fold * eval_width
        b = n if fold == args.folds - 1 else min(n, a + eval_width)
        if b - a < args.slow + 2:
            continue
        history_start = max(0, a - args.slow * 3)
        start_ts = rows[a]["t"]
        fold_result = run_ema_cross_backtest(rows[history_start:b], trade_start_ts=start_ts, **common)
        fold_trades = fold_result["trade_log"]
        walk_results.append({
            "fold": fold + 1, "start_ts": start_ts, "end_ts": rows[b - 1]["t"],
            "bars": b - a, "trades": len(fold_trades),
            "net_return_pct": round(sum(t["pnl_equity"] for t in fold_trades) * 100, 4),
            "win_rate_pct": round(sum(t["pnl_equity"] > 0 for t in fold_trades) / len(fold_trades) * 100, 2) if fold_trades else None,
            "profit_factor": round(sum(t["pnl_equity"] for t in fold_trades if t["pnl_equity"] > 0) / -sum(t["pnl_equity"] for t in fold_trades if t["pnl_equity"] < 0), 4) if any(t["pnl_equity"] < 0 for t in fold_trades) else (None if not any(t["pnl_equity"] > 0 for t in fold_trades) else "INF"),
            "trade_log": fold_trades,
        })

    report = {
        "status": "RESEARCH_ONLY",
        "symbol": args.symbol.upper(),
        "interval_note": "Input CSV interval must be consistent; verify source and range before interpreting metrics.",
        "bars": len(rows), "first_ts": rows[0]["t"], "last_ts": rows[-1]["t"],
        "parameters": common,
        "split": {"train_fraction": args.train_fraction, "boundary_ts": boundary_ts,
                  "warmup_bars": len(warmup), "holdout_bars": len(holdout)},
        "holdout": {
            "bars": len(holdout), "trades": len(holdout_trades),
            "net_return_pct": round(sum(holdout_pnls) * 100, 4),
            "win_rate_pct": round(sum(p > 0 for p in holdout_pnls) / len(holdout_pnls) * 100, 2) if holdout_pnls else None,
            "profit_factor": round(holdout_profit / holdout_loss, 4) if holdout_loss else (None if not holdout_profit else "INF"),
            "expectancy_equity_pct": round(mean(holdout_pnls) * 100, 4) if holdout_pnls else None,
            "trade_log": holdout_trades,
        },
        "walk_forward": walk_results,
        "warnings": [
            "This runner evaluates a fixed EMA baseline; it does not optimize or certify a profitable model.",
            "Trade-level returns are not compounded portfolio returns; inspect the trade log and equity path before interpreting profitability.",
            "Funding is a constant per-bar assumption here, not historical realized funding. Use a historical funding series before production decisions.",
            "All report results require independent review of CSV coverage, exchange data and execution assumptions."
        ],
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"status=RESEARCH_ONLY symbol={args.symbol.upper()} bars={len(rows)} "
          f"holdout_trades={len(holdout_trades)} output={output}")


if __name__ == "__main__":
    main()
