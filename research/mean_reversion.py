"""Research-only mean-reversion model using causal Bollinger z-score and RSI filters."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from statistics import median

from research.run_backtest import load_csv, load_funding_csv


GRID = [
    {"window": window, "entry_z": entry_z, "stop_atr": stop_atr,
     "rsi_filter": rsi_filter, "direction": direction}
    for window in (24, 48, 72)
    for entry_z in (1.5, 2.0)
    for stop_atr in (1.5, 2.0)
    for rsi_filter in (0, 1)
    for direction in ("BOTH", "LONG", "SHORT")
]


def indicators(rows: list[dict], window: int) -> tuple[list[float | None], list[float | None], list[float | None]]:
    mean_line, std_line, rsi_line = [None] * len(rows), [None] * len(rows), [None] * len(rows)
    closes = [float(r["c"]) for r in rows]
    gains, losses = [0.0], [0.0]
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    for i in range(window, len(rows)):
        prior = closes[i-window:i]
        mu = sum(prior) / window
        variance = sum((x-mu)**2 for x in prior) / window
        mean_line[i], std_line[i] = mu, math.sqrt(variance)
        period_gains, period_losses = gains[i-14+1:i+1], losses[i-14+1:i+1]
        if len(period_gains) == 14:
            avg_gain, avg_loss = sum(period_gains)/14, sum(period_losses)/14
            rsi_line[i] = 100.0 if avg_loss == 0 and avg_gain > 0 else (
                50.0 if avg_gain == 0 and avg_loss == 0 else
                0.0 if avg_gain == 0 else 100.0 - 100.0/(1.0 + avg_gain/avg_loss))
    return mean_line, std_line, rsi_line


def atr_series(rows: list[dict], period: int = 14) -> list[float | None]:
    out = [None] * len(rows)
    tr = []
    for i, r in enumerate(rows):
        prev = float(rows[i-1]["c"]) if i else float(r["c"])
        tr.append(max(float(r["h"])-float(r["l"]), abs(float(r["h"])-prev), abs(float(r["l"])-prev)))
        if i >= period-1:
            out[i] = sum(tr[i-period+1:i+1])/period
    return out


def funding_cashflow(side: int, quantity: float, entry_ts: int, exit_ts: int,
                    funding_rates: list[dict] | None) -> tuple[float, int]:
    """Return realized funding cashflow and settlement count for a held position.

    Timestamps are candle-open timestamps. Settlements must be strictly after
    entry-bar open and no later than exit-bar open; exact intrabar order cannot
    be recovered from OHLC candles.
    """
    if side not in (-1, 1) or quantity < 0 or exit_ts < entry_ts:
        raise ValueError("invalid side, quantity, or trade timestamps")
    pnl = 0.0
    count = 0
    for event in funding_rates or []:
        ts = int(event["funding_time"])
        if entry_ts < ts <= exit_ts:
            rate = float(event["funding_rate"])
            mark = float(event["mark_price"])
            if not math.isfinite(rate) or not math.isfinite(mark) or mark <= 0:
                raise ValueError("invalid funding rate or mark price")
            pnl += -side * quantity * mark * rate
            count += 1
    return pnl, count


def simulate(rows: list[dict], params: dict, start: int, end: int,
             fee: float = 0.0005, slippage: float = 0.0002,
             funding_rates: list[dict] | None = None, symbol: str | None = None) -> dict:
    if not 0 <= start < end <= len(rows):
        raise ValueError("invalid evaluation range")
    if any(not math.isfinite(x) or x < 0 for x in (fee, slippage)):
        raise ValueError("fees and slippage must be finite and non-negative")
    window, entry_z, stop_atr, rsi_filter = (
        params["window"], params["entry_z"], params["stop_atr"], params["rsi_filter"])
    direction = params.get("direction", "BOTH")
    if direction not in ("BOTH", "LONG", "SHORT"):
        raise ValueError("direction must be BOTH, LONG, or SHORT")
    if funding_rates is not None:
        if not symbol:
            raise ValueError("symbol is required when funding_rates are supplied")
        if any(event.get("symbol", "").upper() != symbol.upper() for event in funding_rates):
            raise ValueError("funding CSV symbol does not match --symbol")
        if any(not math.isfinite(float(event["funding_rate"])) or
               not math.isfinite(float(event["mark_price"])) or float(event["mark_price"]) <= 0
               for event in funding_rates):
            raise ValueError("funding data contains invalid rates or mark prices")
        funding_rates = sorted(funding_rates, key=lambda event: int(event["funding_time"]))
        if any(int(a["funding_time"]) == int(b["funding_time"])
               for a, b in zip(funding_rates, funding_rates[1:])):
            raise ValueError("duplicate funding timestamp")
    mean_line, std_line, rsi_line = indicators(rows, window)
    atr = atr_series(rows)
    equity, peak, max_dd, trades = 1.0, 1.0, 0.0, []
    i = max(start, window + 14)
    while i < end - 1:
        mu, sd, a = mean_line[i], std_line[i], atr[i]
        if mu is None or sd is None or sd <= 0 or a is None or a <= 0:
            i += 1
            continue
        z = (float(rows[i]["c"]) - mu) / sd
        rsi = rsi_line[i]
        side = 0
        if z <= -entry_z and (not rsi_filter or (rsi is not None and rsi <= 35)):
            side = 1
        elif z >= entry_z and (not rsi_filter or (rsi is not None and rsi >= 65)):
            side = -1
        if direction == "LONG" and side < 0:
            side = 0
        elif direction == "SHORT" and side > 0:
            side = 0
        if not side:
            i += 1
            continue
        entry_idx = i + 1
        entry = float(rows[entry_idx]["o"])
        risk = stop_atr * a
        stop = entry - side * risk
        target = mu
        # Avoid a target that is not on the profitable side of the next-open entry.
        if (target-entry)*side <= 0:
            i += 1
            continue
        qty = min(equity * 0.01 / risk, equity * 3.0 / entry)
        exit_idx, exit_price, reason = end-1, float(rows[end-1]["c"]), "TIME"
        for j in range(entry_idx, end):
            bar = rows[j]
            marked = max(1e-6, equity + qty*(float(bar["c"])-entry)*side
                         - qty*(entry+float(bar["c"]))*(fee+slippage))
            peak = max(peak, marked)
            max_dd = max(max_dd, (peak-marked)/peak if peak else 0)
            stop_hit = float(bar["l"]) <= stop if side == 1 else float(bar["h"]) >= stop
            target_hit = float(bar["h"]) >= target if side == 1 else float(bar["l"]) <= target
            if stop_hit:
                exit_idx = j
                exit_price = min(stop, float(bar["o"])) if side == 1 else max(stop, float(bar["o"]))
                reason = "STOP"
                break
            if target_hit:
                exit_idx, exit_price, reason = j, target, "MEAN_TARGET"
                break
        price_pnl = qty*(exit_price-entry)*side
        trading_cost = qty*(entry+exit_price)*(fee+slippage)
        # Candle timestamps are open times; same-timestamp entry settlements are excluded.
        entry_ts = int(rows[entry_idx]["t"])
        exit_ts = int(rows[exit_idx]["t"])
        funding_pnl, funding_events = funding_cashflow(
            side, qty, entry_ts, exit_ts, funding_rates
        )
        pnl = price_pnl - trading_cost + funding_pnl
        before = equity
        equity = max(1e-6, equity+pnl)
        trades.append({"side":"LONG" if side == 1 else "SHORT", "entry_time":rows[entry_idx]["t"],
                       "exit_time":rows[exit_idx]["t"], "reason":reason,
                       "entry_price":entry, "exit_price":exit_price, "quantity":qty,
                       "price_pnl_equity":price_pnl, "trading_cost_equity":trading_cost,
                       "funding_pnl_equity":funding_pnl, "funding_events":funding_events,
                       "pnl_equity":equity-before, "return_pct":(equity/before-1)*100})
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak-equity)/peak if peak else 0)
        i = exit_idx+1
    wins = [t for t in trades if t["pnl_equity"] > 0]
    losses = [t for t in trades if t["pnl_equity"] < 0]
    gp, gl = sum(t["pnl_equity"] for t in wins), -sum(t["pnl_equity"] for t in losses)
    def side_summary(side: str) -> dict:
        side_trades = [t for t in trades if t["side"] == side]
        side_wins = [t["pnl_equity"] for t in side_trades if t["pnl_equity"] > 0]
        side_losses = [-t["pnl_equity"] for t in side_trades if t["pnl_equity"] < 0]
        side_gp, side_gl = sum(side_wins), sum(side_losses)
        return {
            "trades": len(side_trades),
            "net_pnl_equity_pct": round(sum(t["pnl_equity"] for t in side_trades) * 100, 4),
            "win_rate_pct": round(len(side_wins) / len(side_trades) * 100, 2) if side_trades else None,
            "profit_factor": round(side_gp / side_gl, 4) if side_gl else ("INF" if side_gp else None),
        }
    return {"trades":len(trades), "net_return_pct":round((equity-1)*100,4),
            "max_drawdown_pct":round(max_dd*100,4),
            "win_rate_pct":round(len(wins)/len(trades)*100,2) if trades else None,
            "profit_factor":round(gp/gl,4) if gl else ("INF" if gp else None),
            "long_trades":sum(t["side"]=="LONG" for t in trades),
            "short_trades":sum(t["side"]=="SHORT" for t in trades),
            "long_stats": side_summary("LONG"),
            "short_stats": side_summary("SHORT"),
            "trade_log":trades}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--interval", choices=("1h",), default="1h")
    parser.add_argument("--train-fraction", type=float, default=0.6)
    parser.add_argument("--funding-csv", help="optional historical funding CSV from research.funding_data")
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
        raise ValueError("mean-reversion research requires at least 1000 hourly bars")
    if not 0.3 <= args.train_fraction <= 0.8:
        raise ValueError("train-fraction must be between 0.3 and 0.8")
    split = int(len(rows)*args.train_fraction)
    eval_start = max(100, split//3)
    width = (split-eval_start)//3
    if width < 100:
        raise ValueError("insufficient training data for three folds")
    windows = [(eval_start+n*width, split if n==2 else eval_start+(n+1)*width) for n in range(3)]
    candidates=[]
    for p in GRID:
        folds=[simulate(rows[:b],p,a,b, funding_rates=historical_funding, symbol=args.symbol) for a,b in windows]
        returns=[f["net_return_pct"] for f in folds]
        dds=[f["max_drawdown_pct"] for f in folds]
        positive=sum(x>0 for x in returns)
        worst=min(returns)
        score=median(returns)-0.5*median(dds)
        trades=sum(f["trades"] for f in folds)
        eligible=trades>=12 and all(f["trades"]>=2 for f in folds) and median(returns)>0 and positive>=2 and worst>=-3 and score>0
        candidates.append({"parameters":p,"folds":folds,"median_fold_return_pct":round(median(returns),4),
                           "median_fold_drawdown_pct":round(median(dds),4),"positive_folds":positive,
                           "worst_fold_return_pct":round(worst,4),"score":round(score,4),"eligible":eligible})
    eligible=sorted((x for x in candidates if x["eligible"]),key=lambda x:x["score"],reverse=True)
    selected=eligible[0] if eligible else None
    # Diagnostics only: if no candidate qualifies, evaluate the top-ranked
    # training candidate on holdout, clearly marking it as NOT SELECTED.
    diagnostic=sorted(candidates,key=lambda x:x["score"],reverse=True)[0] if candidates else None
    holdout=stress=None
    if selected:
        holdout=simulate(rows,selected["parameters"],split,len(rows), funding_rates=historical_funding, symbol=args.symbol)
        stress=simulate(rows,selected["parameters"],split,len(rows),fee=0.0005,slippage=0.001, funding_rates=historical_funding, symbol=args.symbol)
    elif diagnostic:
        holdout=simulate(rows,diagnostic["parameters"],split,len(rows), funding_rates=historical_funding, symbol=args.symbol)
        stress=simulate(rows,diagnostic["parameters"],split,len(rows),fee=0.0005,slippage=0.001, funding_rates=historical_funding, symbol=args.symbol)
    report={"status":"RESEARCH_ONLY","symbol":args.symbol.upper(),"interval":args.interval,"bars":len(rows),
            "split":{"train_fraction":args.train_fraction,"train_bars":split,"holdout_bars":len(rows)-split},
            "strategy":"causal rolling mean-reversion: prior-window z-score, optional RSI extreme filter, direction-specific candidate (BOTH/LONG/SHORT), next-open entry, mean target, ATR stop; conservative stop-first same-bar handling",
            "candidate_count":len(candidates),"eligible_count":len(eligible),
            "selected_parameters":selected["parameters"] if selected else None,
            "diagnostic_only_parameters":diagnostic["parameters"] if diagnostic and not selected else None,
            "diagnostic_only_not_selected":bool(diagnostic and not selected),
            "training_selection_metrics":{k:v for k,v in selected.items() if k!="folds"} if selected else None,
            "candidate_diagnostics":sorted(candidates,key=lambda x:x["score"],reverse=True),
            "holdout":{k:v for k,v in holdout.items() if k!="trade_log"} if holdout else None,
            "holdout_interpretation":"SELECTED_CANDIDATE" if selected else "DIAGNOSTIC_ONLY_NOT_SELECTED",
            "stress_holdout_0_30pct_round_trip":{k:v for k,v in stress.items() if k!="trade_log"} if stress else None,
            "funding_data": ({"source": args.funding_csv, "records": len(historical_funding),
                              "first_ts": historical_funding[0]["funding_time"],
                              "last_ts": historical_funding[-1]["funding_time"],
                              "symbol": historical_funding[0]["symbol"],
                              "timing_convention": "funding settlement timestamps strictly after entry-bar open through exit-bar open"}
                             if historical_funding else {"source": None, "records": 0}),
            "funding_pnl_holdout_equity": round(sum(t["funding_pnl_equity"] for t in (holdout or {}).get("trade_log", []))*100, 4),
            "warnings":["Research only; funding is applied only when a historical funding CSV is supplied.",
                        "Funding timing uses candle open timestamps; same-timestamp entry events are excluded because OHLC data cannot resolve intrabar ordering.",
                        "No deployment decision without independent forward validation."]}
    Path(args.output).parent.mkdir(parents=True,exist_ok=True)
    Path(args.output).write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(f"status=RESEARCH_ONLY symbol={args.symbol.upper()} candidates={len(candidates)} eligible={len(eligible)} selected={bool(selected)}")
    print(f"selected_parameters={report['selected_parameters']}")
    print(f"holdout={report['holdout']}")
    print(f"stress_holdout={report['stress_holdout_0_30pct_round_trip']}")
    print(f"funding_data={report['funding_data']} funding_pnl_holdout_equity={report['funding_pnl_holdout_equity']}")
    print("top_training_candidates="+json.dumps([{k:x[k] for k in ("parameters","median_fold_return_pct","median_fold_drawdown_pct","positive_folds","worst_fold_return_pct","score","eligible")} for x in sorted(candidates,key=lambda x:x["score"],reverse=True)[:3]]))


if __name__ == "__main__":
    main()
