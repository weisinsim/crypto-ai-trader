"""Breakout -> retest -> 1H confirmation research strategy; research-only."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from statistics import median

from research.run_backtest import load_csv, load_funding_csv

GRID = [
    {"lookback": lookback, "max_wait": max_wait, "target_r": target_r,
     "trend_period": trend_period, "volatility_filter": volatility_filter, "direction": direction,
     "breakout_atr": breakout_atr, "retest_atr": retest_atr, "body_confirm_atr": body_confirm_atr}
    for lookback in (24, 48, 72)
    for max_wait in (3, 6)
    for target_r in (1.5, 2.0, 2.5, 3.0)
    for trend_period in (0, 100, 200)
    for volatility_filter in (0.0, 1.0, 1.2)
    for direction in ("BOTH", "LONG", "SHORT")
    for breakout_atr in (0.05, 0.15)
    for retest_atr in (0.20, 0.40)
    for body_confirm_atr in (0.0, 0.15)
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


def _strict_funding_timestamp(value) -> int:
    """Accept integer millisecond timestamps without truncating malformed values."""
    import re
    if isinstance(value, bool):
        raise ValueError("funding timestamp must be an integer")
    if isinstance(value, int):
        ts = value
    elif isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
        ts = int(value.strip())
    else:
        raise ValueError("funding timestamp must be an integer")
    if ts < 0:
        raise ValueError("invalid funding timestamp")
    return ts


def funding_cashflow(side: int, quantity: float, entry_ts: int, exit_ts: int,
                    funding_rates: list[dict] | None) -> tuple[float, int]:
    """Calculate held-position funding cashflow; exact entry-time settlements are excluded."""
    if (side not in (-1, 1) or not math.isfinite(quantity) or quantity < 0
            or entry_ts < 0 or exit_ts < entry_ts):
        raise ValueError("invalid side, quantity, or trade timestamps")
    pnl = 0.0
    count = 0
    seen_timestamps = set()
    for event in funding_rates or []:
        ts = _strict_funding_timestamp(event["funding_time"])
        if ts in seen_timestamps:
            raise ValueError("duplicate funding timestamp")
        seen_timestamps.add(ts)
        rate = float(event["funding_rate"])
        mark = float(event["mark_price"])
        if not math.isfinite(rate) or not math.isfinite(mark) or mark <= 0:
            raise ValueError("invalid funding rate or mark price")
        if entry_ts < ts <= exit_ts:
            pnl += -side * quantity * mark * rate
            count += 1
    return pnl, count


def marked_equity_after_costs_and_funding(equity: float, mark_pnl: float,
                                       mark_costs: float, mark_funding: float) -> float:
    """Return conservative intratrade equity after unrealized PnL, costs, and accrued funding."""
    values = (equity, mark_pnl, mark_costs, mark_funding)
    if any(not math.isfinite(value) for value in values) or equity <= 0 or mark_costs < 0:
        raise ValueError("invalid marked-equity inputs")
    return max(0.000001, equity + mark_pnl - mark_costs + mark_funding)


def run_breakout_retest(rows: list[dict], params: dict, start_idx: int, end_idx: int,
                        fee_rate: float = 0.0005, slippage_rate: float = 0.0002,
                        risk_fraction: float = 0.01, max_leverage: float = 3.0,
                        funding_rates: list[dict] | None = None, symbol: str | None = None) -> dict:
    """Simulate long and short separately; entries occur at next bar open after confirmation."""
    if not (0 <= start_idx < end_idx <= len(rows)):
        raise ValueError("invalid evaluation indices")
    if not (0 < risk_fraction <= 0.05) or max_leverage <= 0:
        raise ValueError("invalid risk or leverage setting")
    if any(not math.isfinite(x) or x < 0 for x in (fee_rate, slippage_rate)):
        raise ValueError("fees and slippage must be finite and non-negative")
    if funding_rates is not None:
        if not symbol or any(event.get("symbol", "").upper() != symbol.upper() for event in funding_rates):
            raise ValueError("funding CSV symbol does not match --symbol")
        if any(not math.isfinite(float(event["funding_rate"])) or
               not math.isfinite(float(event["mark_price"])) or float(event["mark_price"]) <= 0
               for event in funding_rates):
            raise ValueError("funding data contains invalid rates or mark prices")
        funding_timestamps = [_strict_funding_timestamp(event["funding_time"]) for event in funding_rates]
        if any(ts < 0 for ts in funding_timestamps):
            raise ValueError("funding timestamps must be non-negative")
        if len(funding_timestamps) != len(set(funding_timestamps)):
            raise ValueError("duplicate funding timestamp")
        funding_rates = sorted(funding_rates, key=lambda event: _strict_funding_timestamp(event["funding_time"]))
    lookback, max_wait, target_r = params["lookback"], params["max_wait"], params["target_r"]
    trend_period = params.get("trend_period", 0)
    volatility_filter = params.get("volatility_filter", 0.0)
    direction_filter = params.get("direction", "BOTH")
    breakout_atr = params.get("breakout_atr", 0.05)
    retest_atr = params.get("retest_atr", 0.30)
    body_confirm_atr = params.get("body_confirm_atr", 0.0)
    if (lookback < 5 or max_wait < 1 or target_r <= 0
            or trend_period not in (0, 100, 200)
            or volatility_filter not in (0.0, 1.0, 1.2)
            or direction_filter not in ("BOTH", "LONG", "SHORT")
            or breakout_atr not in (0.05, 0.15)
            or retest_atr not in (0.20, 0.30, 0.40)
            or body_confirm_atr not in (0.0, 0.15)):
        raise ValueError("invalid strategy parameters")
    atr = atr_values(rows)
    trend_ema = ema_values(rows, trend_period)
    atr_median = [None] * len(rows)
    for z in range(48, len(rows)):
        prior_atr = [v for v in atr[z - 48:z] if v is not None and v > 0]
        if prior_atr:
            atr_median[z] = median(prior_atr)
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
            if volatility_filter > 0:
                baseline_atr = atr_median[j]
                if baseline_atr is None or atr[j] < baseline_atr * volatility_filter:
                    continue
            prior = rows[max(0, j - lookback):j]
            if len(prior) < lookback:
                continue
            resistance = max(x["h"] for x in prior)
            support = min(x["l"] for x in prior)
            if rows[j]["c"] > resistance + breakout_atr * atr[j]:
                if direction_filter in ("BOTH", "LONG") and (trend_period == 0 or (trend_ema[j] is not None and rows[j]["c"] > trend_ema[j])):
                    signal = (1, resistance, j)
            elif rows[j]["c"] < support - breakout_atr * atr[j]:
                if direction_filter in ("BOTH", "SHORT") and (trend_period == 0 or (trend_ema[j] is not None and rows[j]["c"] < trend_ema[j])):
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
            tol = retest_atr * atr[k]
            row = rows[k]
            if side == 1:
                retested = row["l"] <= level + tol and row["l"] >= level - 1.5 * tol
                confirms = (row["c"] > level and row["c"] > row["o"]
                            and row["c"] - row["o"] >= body_confirm_atr * atr[k])
                stop = min(row["l"], level) - 0.15 * atr[k]
            else:
                retested = row["h"] >= level - tol and row["h"] <= level + 1.5 * tol
                confirms = (row["c"] < level and row["c"] < row["o"]
                            and row["o"] - row["c"] >= body_confirm_atr * atr[k])
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
                    # Include accrued settlement cashflows in intratrade drawdown,
                    # not only in final realized PnL.
                    mark_funding, _ = funding_cashflow(
                        side, qty, int(rows[entry_idx]["t"]), int(bar["t"]), funding_rates
                    )
                    marked_equity = marked_equity_after_costs_and_funding(equity, mark_pnl, mark_costs, mark_funding)
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
                entry_ts = int(rows[entry_idx]["t"])
                exit_ts = int(rows[exit_idx]["t"])
                # Exclude settlement exactly at entry-bar open; OHLC data cannot
                # resolve whether the position existed before that settlement.
                funding_pnl, funding_events = funding_cashflow(
                    side, qty, entry_ts, exit_ts, funding_rates
                )
                pnl = gross - costs + funding_pnl
                before = equity
                equity = max(0.000001, equity + pnl)
                trades.append({
                    "side": "LONG" if side == 1 else "SHORT",
                    "breakout_time": rows[breakout_idx]["t"],
                    "entry_time": rows[entry_idx]["t"], "exit_time": rows[exit_idx]["t"],
                    "entry": round(entry, 8), "stop": round(stop, 8), "target": round(target, 8),
                    "exit": round(exit_price, 8), "reason": reason,
                    "pnl_equity": round(equity - before, 8),
                    "gross_pnl_equity": round(gross, 8),
                    "trading_cost_equity": round(costs, 8),
                    "funding_pnl_equity": round(funding_pnl, 8),
                    "funding_events": funding_events,
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


def profit_factor_above_one(value) -> bool:
    """Accept infinite PF when there are profits and no losses; reject null/NaN."""
    if value == "INF":
        return True
    return isinstance(value, (int, float)) and math.isfinite(value) and value > 1


def candidate_rejection_reasons(folds: list[dict], score: float) -> list[str]:
    """Explain training-fold rejection without relaxing any acceptance thresholds."""
    if not folds:
        return ["missing_folds"]
    returns = [float(f["net_return_pct"]) for f in folds]
    trades = [int(f["trades"]) for f in folds]
    reasons = []
    if sum(trades) < 12:
        reasons.append("total_trades_below_12")
    if any(n < 2 for n in trades):
        reasons.append("fold_has_fewer_than_2_trades")
    if median(returns) <= 0:
        reasons.append("median_fold_return_not_positive")
    if sum(x > 0 for x in returns) < 2:
        reasons.append("fewer_than_2_positive_folds")
    if min(returns) < -3:
        reasons.append("worst_fold_below_minus_3pct")
    if score <= 0:
        reasons.append("risk_adjusted_score_not_positive")
    return reasons


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--interval", choices=("1h",), default="1h")
    parser.add_argument("--train-fraction", type=float, default=0.6)
    parser.add_argument("--funding-csv", help="optional historical funding CSV")
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
        folds = [run_breakout_retest(rows[:b], params, a, b, funding_rates=historical_funding, symbol=args.symbol) for a, b in windows]
        fold_returns = [f["net_return_pct"] for f in folds]
        fold_dd = [f["max_drawdown_pct"] for f in folds]
        trade_count = sum(f["trades"] for f in folds)
        positive_folds = sum(x > 0 for x in fold_returns)
        worst_fold = min(fold_returns)
        score = median(fold_returns) - 0.5 * median(fold_dd)
        rejection_reasons = candidate_rejection_reasons(folds, score)
        eligible = not rejection_reasons
        candidates.append({"parameters": params, "folds": folds, "median_fold_return_pct": round(median(fold_returns), 4),
                           "median_fold_drawdown_pct": round(median(fold_dd), 4), "score": round(score, 4),
                           "positive_folds": positive_folds, "worst_fold_return_pct": round(worst_fold, 4),
                           "rejection_reasons": rejection_reasons, "eligible": eligible})
    eligible = sorted((x for x in candidates if x["eligible"]), key=lambda x: x["score"], reverse=True)
    selected = eligible[0] if eligible else None
    direction_summary = {}
    for direction in ("LONG", "SHORT", "BOTH"):
        group = [item for item in candidates if item["parameters"]["direction"] == direction]
        passing = sorted((item for item in group if item["eligible"]), key=lambda item: item["score"], reverse=True)
        best = passing[0] if passing else max(group, key=lambda item: item["score"])
        direction_summary[direction] = {
            "candidate_count": len(group),
            "eligible_count": len(passing),
            "best_parameters": best["parameters"],
            "best_candidate_eligible": best["eligible"],
            "best_score": best["score"],
            "best_median_fold_return_pct": best["median_fold_return_pct"],
            "best_worst_fold_return_pct": best["worst_fold_return_pct"],
            "best_rejection_reasons": best["rejection_reasons"],
        }
    holdout = stress = severe = None
    if selected:
        params = selected["parameters"]
        holdout = run_breakout_retest(rows, params, split, len(rows), funding_rates=historical_funding, symbol=args.symbol)
        stress = run_breakout_retest(rows, params, split, len(rows), fee_rate=0.0005, slippage_rate=0.001, funding_rates=historical_funding, symbol=args.symbol)
        severe = run_breakout_retest(rows, params, split, len(rows), fee_rate=0.0005, slippage_rate=0.0018, funding_rates=historical_funding, symbol=args.symbol)
    # Diagnostic only: each direction's candidate is selected on training data,
    # then evaluated on the same holdout. These are not independent validation samples.
    direction_holdout = {}
    for direction in ("LONG", "SHORT", "BOTH"):
        group = [item for item in candidates if item["parameters"]["direction"] == direction and item["eligible"]]
        if not group:
            direction_holdout[direction] = {"status": "NO_ELIGIBLE_TRAINING_CANDIDATE"}
            continue
        candidate = max(group, key=lambda item: item["score"])
        params = candidate["parameters"]
        results = [
            run_breakout_retest(rows, params, split, len(rows), funding_rates=historical_funding, symbol=args.symbol),
            run_breakout_retest(rows, params, split, len(rows), fee_rate=0.0005, slippage_rate=0.001, funding_rates=historical_funding, symbol=args.symbol),
            run_breakout_retest(rows, params, split, len(rows), fee_rate=0.0005, slippage_rate=0.0018, funding_rates=historical_funding, symbol=args.symbol),
        ]
        compact = [{k: value for k, value in result.items() if k != "trade_log"} for result in results]
        passed = all(
            isinstance(item.get("net_return_pct"), (int, float)) and math.isfinite(item["net_return_pct"])
            and item["net_return_pct"] > 0
            and profit_factor_above_one(item.get("profit_factor"))
            for item in compact
        )
        direction_holdout[direction] = {
            "status": "PASS_PRELIMINARY_ONLY" if passed else "FAIL_COST_ROBUSTNESS",
            "parameters": params,
            "training_score": candidate["score"],
            "base_cost_holdout": compact[0],
            "stress_0_30pct_round_trip": compact[1],
            "severe_0_46pct_round_trip": compact[2],
            "not_production_approval": True,
        }
    report = {
        "status": "RESEARCH_ONLY", "symbol": args.symbol.upper(), "interval": args.interval,
        "bars": len(rows), "first_ts": rows[0]["t"], "last_ts": rows[-1]["t"],
        "split": {"train_fraction": args.train_fraction, "train_bars": split, "holdout_bars": len(rows)-split},
        "strategy": "confirmed breakout, retest within 0.30 ATR, directional 1H candle close confirmation, next-bar-open entry; optional causal EMA trend filter (0/100/200); stop wins same-bar stop/target tie; max drawdown includes intratrade close-marked equity",
        "grid": GRID, "candidate_count": len(candidates), "eligible_count": len(eligible),
        "rejection_reason_counts": {reason: sum(reason in item["rejection_reasons"] for item in candidates)
                                    for reason in sorted({reason for item in candidates for reason in item["rejection_reasons"]})},
        "direction_summary": direction_summary,
        "direction_holdout_diagnostics": direction_holdout,
        "selected_parameters": selected["parameters"] if selected else None,
        "training_selection_metrics": {k:v for k,v in selected.items() if k != "folds"} if selected else None,
        "candidate_diagnostics": sorted(candidates, key=lambda x: x["score"], reverse=True),
        "holdout": {k:v for k,v in holdout.items() if k != "trade_log"} | {"trades_by_side": {"long": holdout["long"], "short": holdout["short"]}} if holdout else None,
        "stress_holdout_0_30pct_round_trip": {k:v for k,v in stress.items() if k != "trade_log"} | {"trades_by_side": {"long": stress["long"], "short": stress["short"]}} if stress else None,
        "severe_holdout_0_46pct_round_trip": {k:v for k,v in severe.items() if k != "trade_log"} | {"trades_by_side": {"long": severe["long"], "short": severe["short"]}} if severe else None,
        "funding_data": ({"records": len(historical_funding), "symbol": args.symbol.upper(),
                          "mark_price_sources": {source: sum(1 for event in historical_funding if event.get("mark_price_source", "unspecified") == source)
                                                 for source in sorted({event.get("mark_price_source", "unspecified") for event in historical_funding})}}
                         if historical_funding is not None else {"records": 0, "source": None}),
        "cost_robustness_gate": ({"status": "NOT_EVALUATED"} if not selected else
            {"status": "PASS_PRELIMINARY_ONLY" if all(
                isinstance(item.get("net_return_pct"), (int, float)) and math.isfinite(item["net_return_pct"])
                and item["net_return_pct"] > 0
                and profit_factor_above_one(item.get("profit_factor"))
                for item in (holdout, stress, severe)) else "FAIL_COST_ROBUSTNESS",
             "not_production_approval": True}),
        "warnings": ["Research only; funding is included only when historical funding CSV is supplied.",
                     "Funding settlement timestamps are strictly after entry-bar open through exit-bar open.",
                     "OHLCV cannot resolve intrabar order; stop is conservatively prioritized.",
                     "No deployment decision without independent forward validation."],
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"status=RESEARCH_ONLY symbol={args.symbol.upper()} bars={len(rows)} candidates={len(candidates)} eligible={len(eligible)} selected={bool(selected)}")
    print(f"direction_summary={report['direction_summary']}")
    print(f"direction_holdout_diagnostics={report['direction_holdout_diagnostics']}")
    print(f"selected_parameters={report['selected_parameters']}")
    print(f"holdout={report['holdout']}")
    print(f"stress_holdout={report['stress_holdout_0_30pct_round_trip']}")
    print(f"severe_holdout={report['severe_holdout_0_46pct_round_trip']}")
    print(f"funding_data={report['funding_data']}")
    print(f"cost_robustness_gate={report['cost_robustness_gate']}")
    print("top_training_candidates=" + json.dumps([
        {k: item[k] for k in ("parameters", "median_fold_return_pct",
                              "median_fold_drawdown_pct", "positive_folds",
                              "worst_fold_return_pct", "score", "eligible")}
        for item in sorted(candidates, key=lambda item: item["score"], reverse=True)[:3]
    ]))


if __name__ == "__main__":
    main()
