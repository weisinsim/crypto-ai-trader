"""Breakout -> retest -> 1H confirmation research strategy; research-only."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from statistics import median

from research.run_backtest import load_csv

GRID = [
    {"lookback": lookback, "max_wait": max_wait, "target_r": target_r, "trend_period": trend_period}
    for lookback in (24, 48, 72)
    for max_wait in (3, 6)
    for target_r in (1.5, 2.0)
    for trend_period in (0, 100, 200)
]


def atr_values(rows: list[dict], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(rows)
    trs = []
    for i, row in enumerate(rows):
        prev_close = rows[i - 1]["c"] if i else row["c"]
        tr = max(row["h"] - row["l"], abs(row["h"] - prev_close), abs(row["l"] - prev_close))
        trs.append(tr)
        if i >= period - 1:
            out[i] = sum(trs[i - period + 1:i + 1]) / period
    return out


def ema_values(rows: list[dict], period: int) -> list[float | None]:
    """Causal EMA series; each value uses closes up to and including that bar."""
    out: list[float | None] = [None] * len(rows)
    if period <= 0:
        return out
    alpha = 2.0 / (period + 1)
    value = None
    for i, row in enumerate(rows):
        close = float(row["c"])
        value = close if value is None else alpha * close + (1 - alpha) * value
        if i >= period - 1:
            out[i] = value
    return out


def run_breakout_retest(rows: list[dict], params: dict, start_idx: int, end_idx: int,
                        fee_rate: float = 0.0005, slippage_rate: float = 0.0002,
                        risk_fraction: float = 0.01, max_leverage: float = 3.0) -> dict:
    """Simulate long and short separately; entries occur at next bar open after confirmation."""
    if not (0 <= start_idx < end_idx <= len(rows)):
        raise ValueError("invalid evaluation indices")
    if not (0 < risk_fraction <= 0.05) or max_leverage <= 0:
        raise ValueError("invalid risk or leverage setting")
    if any(not math.isfinite(x) or x < 0 for x in (fee_rate, slippage_rate)):
        raise ValueError("fees and slippage must be finite and non-negative")
    lookback, max_wait, target_r = params["lookback"], params["max_wait"], params["target_r"]
    trend_period = params.get("trend_period", 0)
    if lookback < 5 or max_wait < 1 or target_r <= 0 or trend_period not in (0, 100, 200):
        raise ValueError("invalid strategy parameters")
    atr = atr_values(rows)
    trend_ema = ema_values(rows, trend_period)
    trades = []
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    i = max(start_idx, lookback + 15)
    while i < end_idx - 1:
        # Search a close-confirmed range break using only bars before the signal bar.
        signal = None
        for j in range(i, min(end_idx - 1, i + 1)):
            if atr[j] is None or atr[j] <= 0:
                continue
            prior = rows[max(0, j - lookback):j]
            if len(prior) < lookback:
                continue
            resistance = max(x["h"] for x in prior)
            support = min(x["l"] for x in prior)
            if rows[j]["c"] > resistance + 0.05 * atr[j]:
                if trend_period == 0 or (trend_ema[j] is not None and rows[j]["c"] > trend_ema[j]):
                    signal = (1, resistance, j)
            elif rows[j]["c"] < support - 0.05 * atr[j]:
                if trend_period == 0 or (trend_ema[j] is not None and rows[j]["c"] < trend_ema[j]):
                    signal = (-1, support, j)
        if signal is None:
            i += 1
            continue
        side, level, breakout_idx = signal
        confirmed = None
        search_end = min(end_idx - 1, breakout_idx + max_wait + 1)
        for k in range(breakout_idx + 1, search_end):
            if atr[k] is None or atr[k] <= 0:
                continue
            tol = 0.30 * atr[k]
            row = rows[k]
            if side == 1:
                retested = row["l"] <= level + tol and row["l"] >= level - 1.5 * tol
                confirms = row["c"] > level and row["c"] > row["o"]
                stop = min(row["l"], level) - 0.15 * atr[k]
            else:
                retested = row["h"] >= level - tol and row["h"] <= level + 1.5 * tol
                confirms = row["c"] < level and row["c"] < row["o"]
                stop = max(row["h"], level) + 0.15 * atr[k]
            if retested and confirms:
                entry_idx = k + 1
                if entry_idx >= end_idx:
                    break
                entry = rows[entry_idx]["o"]
                risk = abs(entry - stop)
                if risk <= 0 or not math.isfinite(risk):
                    continue
                target = entry + side * target_r * risk
                qty = min(equity * risk_fraction / risk, equity * max_leverage / entry)
                exit_idx, exit_price, reason = end_idx - 1, rows[end_idx - 1]["c"], "TIME"
                for m in range(entry_idx, end_idx):
                    bar = rows[m]
                    # Track unrealized equity while the position is open so max DD
                    # includes intratrade adverse excursions, not only closed trades.
                    mark_pnl = qty * (float(bar["c"]) - entry) * side
                    mark_costs = qty * (entry + float(bar["c"])) * (fee_rate + slippage_rate)
                    marked_equity = max(0.000001, equity + mark_pnl - mark_costs)
                    peak = max(peak, marked_equity)
                    if peak > 0:
                        max_dd = max(max_dd, (peak - marked_equity) / peak)
                    stop_hit = bar["l"] <= stop if side == 1 else bar["h"] >= stop
                    target_hit = bar["h"] >= target if side == 1 else bar["l"] <= target
                    # Conservative tie-break: if both barriers touch in one candle, stop wins.
                    if stop_hit:
                        exit_idx = m
                        exit_price = (min(stop, float(bar["o"])) if side == 1
                                      else max(stop, float(bar["o"])))
                        reason = "STOP"
                        break
                    if target_hit:
                        exit_idx, exit_price, reason = m, target, "TARGET"
                        break
                gross = qty * (exit_price - entry) * side
                costs = qty * (entry + exit_price) * (fee_rate + slippage_rate)
                pnl = gross - costs
                before = equity
                equity = max(0.000001, equity + pnl)
                trades.append({
                    "side": "LONG" if side == 1 else "SHORT",
                    "breakout_time": rows[breakout_idx]["t"],
                    "entry_time": rows[entry_idx]["t"], "exit_time": rows[exit_idx]["t"],
                    "entry": round(entry, 8), "stop": round(stop, 8), "target": round(target, 8),
                    "exit": round(exit_price, 8), "reason": reason,
                    "pnl_equity": round(equity - before, 8),
                    "return_pct": round((equity / before - 1) * 100, 5),
                })
                peak = max(peak, equity)
                if peak > 0:
                    max_dd = max(max_dd, (peak - equity) / peak)
                i = exit_idx + 1
                confirmed = k
                break
        if confirmed is None:
            i = breakout_idx + max_wait + 1
    long_trades = [t for t in trades if t["side"] == "LONG"]
    short_trades = [t for t in trades if t["side"] == "SHORT"]
    def stats(group):
        wins = sum(t["pnl_equity"] > 0 for t in group)
        gp = sum(t["pnl_equity"] for t in group if t["pnl_equity"] > 0)
        gl = -sum(t["pnl_equity"] for t in group if t["pnl_equity"] < 0)
        return {
            "trades": len(group),
            "win_rate_pct": round(wins / len(group) * 100, 2) if group else None,
            "net_return_pct": round((math.prod(1 + t["return_pct"] / 100 for t in group) - 1) * 100, 4) if group else 0.0,
            "profit_factor": round(gp / gl, 4) if gl else ("INF" if gp else None),
        }
    return {
        "trades": len(trades), "net_return_pct": round((equity - 1) * 100, 4),
        "max_drawdown_pct": round(max_dd * 100, 4),
        "win_rate_pct": round(sum(t["pnl_equity"] > 0 for t in trades) / len(trades) * 100, 2) if trades else None,
        "profit_factor": (round(sum(t["pnl_equity"] for t in trades if t["pnl_equity"] > 0) /
                                 -sum(t["pnl_equity"] for t in trades if t["pnl_equity"] < 0), 4)
                          if any(t["pnl_equity"] < 0 for t in trades) else ("INF" if trades else None)),
        "long": stats(long_trades), "short": stats(short_trades), "trade_log": trades,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--interval", choices=("1h",), default="1h")
    parser.add_argument("--train-fraction", type=float, default=0.6)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    rows = load_csv(args.csv, 3_600_000)
    if len(rows) < 1000:
        raise ValueError("breakout/retest research requires at least 1000 hourly bars")
    if not 0.3 <= args.train_fraction <= 0.8:
        raise ValueError("train-fraction must be between 0.3 and 0.8")
    split = int(len(rows) * args.train_fraction)
    train_end = split
    warm = 100
    train_folds = []
    eval_start = max(warm, train_end // 3)
    width = (train_end - eval_start) // 3
    if width < 100:
        raise ValueError("insufficient training bars for three folds")
    windows = [(eval_start + n * width, train_end if n == 2 else eval_start + (n + 1) * width)
               for n in range(3)]
    candidates = []
    for params in GRID:
        folds = [run_breakout_retest(rows[:b], params, a, b) for a, b in windows]
        fold_returns = [f["net_return_pct"] for f in folds]
        fold_dd = [f["max_drawdown_pct"] for f in folds]
        trade_count = sum(f["trades"] for f in folds)
        positive_folds = sum(x > 0 for x in fold_returns)
        worst_fold = min(fold_returns)
        score = median(fold_returns) - 0.5 * median(fold_dd)
        eligible = (trade_count >= 12 and all(f["trades"] >= 2 for f in folds)
                    and median(fold_returns) > 0 and positive_folds >= 2 and worst_fold >= -3
                    and score > 0)
        candidates.append({"parameters": params, "folds": folds, "median_fold_return_pct": round(median(fold_returns), 4),
                           "median_fold_drawdown_pct": round(median(fold_dd), 4), "score": round(score, 4),
                           "positive_folds": positive_folds, "worst_fold_return_pct": round(worst_fold, 4),
                           "eligible": eligible})
    eligible = sorted((x for x in candidates if x["eligible"]), key=lambda x: x["score"], reverse=True)
    selected = eligible[0] if eligible else None
    holdout = stress = None
    if selected:
        params = selected["parameters"]
        holdout = run_breakout_retest(rows, params, split, len(rows))
        stress = run_breakout_retest(rows, params, split, len(rows), fee_rate=0.0005, slippage_rate=0.001)
        # Summaries only in the report; keep detailed trades to aid audit.
    report = {
        "status": "RESEARCH_ONLY", "symbol": args.symbol.upper(), "interval": args.interval,
        "bars": len(rows), "first_ts": rows[0]["t"], "last_ts": rows[-1]["t"],
        "split": {"train_fraction": args.train_fraction, "train_bars": split, "holdout_bars": len(rows)-split},
        "strategy": "confirmed breakout, retest within 0.30 ATR, directional 1H candle close confirmation, next-bar-open entry; optional causal EMA trend filter (0/100/200); stop wins same-bar stop/target tie; max drawdown includes intratrade close-marked equity",
        "grid": GRID, "candidate_count": len(candidates), "eligible_count": len(eligible),
        "selected_parameters": selected["parameters"] if selected else None,
        "training_selection_metrics": {k:v for k,v in selected.items() if k != "folds"} if selected else None,
        "candidate_diagnostics": sorted(candidates, key=lambda x: x["score"], reverse=True),
        "holdout": {k:v for k,v in holdout.items() if k != "trade_log"} | {"trades_by_side": {"long": holdout["long"], "short": holdout["short"]}} if holdout else None,
        "stress_holdout_0_30pct_round_trip": {k:v for k,v in stress.items() if k != "trade_log"} | {"trades_by_side": {"long": stress["long"], "short": stress["short"]}} if stress else None,
        "warnings": ["Research only; historical funding rates are not included.", "OHLCV cannot resolve intrabar order; stop is conservatively prioritized.", "No deployment decision without independent forward validation."],
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"status=RESEARCH_ONLY symbol={args.symbol.upper()} bars={len(rows)} candidates={len(candidates)} eligible={len(eligible)} selected={bool(selected)}")
    print(f"selected_parameters={report['selected_parameters']}")
    print(f"holdout={report['holdout']}")
    print(f"stress_holdout={report['stress_holdout_0_30pct_round_trip']}")
    print("top_training_candidates=" + json.dumps([
        {k: item[k] for k in ("parameters", "median_fold_return_pct",
                              "median_fold_drawdown_pct", "positive_folds",
                              "worst_fold_return_pct", "score", "eligible")}
        for item in sorted(candidates, key=lambda item: item["score"], reverse=True)[:3]
    ]))


if __name__ == "__main__":
    main()
