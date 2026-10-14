"""Benchmark public strategy archetypes on 1H data; research only, long-only."""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path


STRATEGIES = ("rsi_mean_reversion", "sma200_trend", "donchian_breakout")
COSTS = {"base": 0.0014, "stress": 0.0030, "severe": 0.0046}


def load_candles(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    rows = [{"t": int(r["t"]), "o": float(r["o"]), "h": float(r["h"]),
             "l": float(r["l"]), "c": float(r["c"]), "v": float(r.get("v", 0))}
            for r in rows]
    if len(rows) < 250:
        raise ValueError("need at least 250 hourly candles for warmed-up indicators")
    if any(not all(math.isfinite(r[k]) for k in ("o", "h", "l", "c", "v")) or
           min(r["o"], r["h"], r["l"], r["c"]) <= 0 or r["v"] < 0
           for r in rows):
        raise ValueError("invalid candle values")
    if any(r["h"] < max(r["o"], r["c"], r["l"]) or
           r["l"] > min(r["o"], r["c"], r["h"]) for r in rows):
        raise ValueError("inconsistent OHLC candle: high/low do not contain open and close")
    if any(rows[i]["t"] < 0 for i in range(len(rows))):
        raise ValueError("candle timestamps must be non-negative")
    if any(rows[i]["t"] >= rows[i+1]["t"] for i in range(len(rows)-1)):
        raise ValueError("candle timestamps must be unique and ascending")
    if any(rows[i+1]["t"] - rows[i]["t"] != 3_600_000
           for i in range(len(rows)-1)):
        raise ValueError("hourly candle timestamps must be contiguous with no missing bars")
    return rows


def sma(values: list[float], period: int) -> list[float | None]:
    out = [None] * len(values)
    total = 0.0
    for i, value in enumerate(values):
        total += value
        if i >= period:
            total -= values[i-period]
        if i >= period-1:
            out[i] = total / period
    return out


def rsi(values: list[float], period: int = 14) -> list[float | None]:
    out = [None] * len(values)
    gains = [0.0] * len(values)
    losses = [0.0] * len(values)
    for i in range(1, len(values)):
        delta = values[i] - values[i-1]
        gains[i], losses[i] = max(delta, 0), max(-delta, 0)
    if len(values) <= period:
        return out
    avg_gain = sum(gains[1:period+1]) / period
    avg_loss = sum(losses[1:period+1]) / period
    def value(g, l):
        return 100.0 if l == 0 and g > 0 else (50.0 if l == 0 else 100 - 100/(1+g/l))
    out[period] = value(avg_gain, avg_loss)
    for i in range(period+1, len(values)):
        avg_gain = (avg_gain*(period-1) + gains[i]) / period
        avg_loss = (avg_loss*(period-1) + losses[i]) / period
        out[i] = value(avg_gain, avg_loss)
    return out


def signals(rows: list[dict], strategy: str) -> tuple[list[bool], list[bool]]:
    close = [r["c"] for r in rows]
    enter, exit_ = [False]*len(rows), [False]*len(rows)
    if strategy == "rsi_mean_reversion":
        vals = rsi(close, 14)
        for i in range(2, len(rows)):
            if vals[i-1] is not None and vals[i] is not None:
                enter[i] = vals[i-1] >= 30 and vals[i] < 30
                exit_[i] = vals[i-1] <= 50 and vals[i] > 50
    elif strategy == "sma200_trend":
        vals = sma(close, 200)
        for i in range(1, len(rows)):
            if vals[i-1] is not None and vals[i] is not None:
                enter[i] = close[i-1] <= vals[i-1] and close[i] > vals[i]
                exit_[i] = close[i-1] >= vals[i-1] and close[i] < vals[i]
    elif strategy == "donchian_breakout":
        for i in range(20, len(rows)):
            upper = max(r["h"] for r in rows[i-20:i])
            lower = min(r["l"] for r in rows[i-10:i])
            enter[i] = close[i] > upper
            exit_[i] = close[i] < lower
    else:
        raise ValueError(f"unknown strategy: {strategy}")
    return enter, exit_


def load_funding(path: str | None) -> list[dict]:
    if not path:
        return []
    with open(path, newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    parsed = []
    for row in rows:
        try:
            timestamp = int(row["funding_time"])
            rate = float(row["funding_rate"])
            mark = float(row["mark_price"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError("invalid funding timestamp, rate, or mark price") from exc
        if timestamp < 0:
            raise ValueError("funding timestamps must be non-negative")
        if not math.isfinite(rate) or not math.isfinite(mark) or mark <= 0:
            raise ValueError("invalid funding rate or mark price")
        parsed.append({"t": timestamp, "rate": rate, "mark": mark})
    if any(parsed[i]["t"] >= parsed[i+1]["t"] for i in range(len(parsed)-1)):
        raise ValueError("funding timestamps must be unique and ascending")
    return parsed


def funding_cost(events: list[dict], entry_ts: int, exit_ts: int, entry_price: float) -> float:
    # Long-only position: positive funding is paid; negative funding is received.
    # Strict bounds avoid charging an event at or before entry / at exit.
    return sum(event["rate"] * event["mark"] / entry_price
               for event in events if entry_ts < event["t"] <= exit_ts)


def simulate(rows: list[dict], strategy: str, start: int, end: int, round_trip_cost: float,
             funding_events: list[dict] | None = None) -> dict:
    funding_events = funding_events or []
    entries, exits = signals(rows, strategy)
    equity, peak, max_dd = 1.0, 1.0, 0.0
    trades, wins, gross_win, gross_loss = [], 0, 0.0, 0.0
    entry_price = None
    i = max(start, 201)
    while i < end - 1:
        if entry_price is not None:
            # Conservative intrabar drawdown: assume the candle high precedes its low.
            # This deliberately errs toward larger drawdown because OHLC does not reveal path.
            favorable_equity = equity * max(0.000001, rows[i]["h"] / entry_price - round_trip_cost)
            peak = max(peak, favorable_equity)
            adverse_equity = equity * max(0.000001, rows[i]["l"] / entry_price - round_trip_cost)
            max_dd = max(max_dd, (peak-adverse_equity)/peak)
        if entry_price is None:
            if entries[i]:
                entry_price = rows[i+1]["o"]
                entry_i = i+1
                i += 1
                continue
        elif exits[i]:
            exit_price = rows[i+1]["o"]
            gross = exit_price / entry_price - 1
            funding = funding_cost(funding_events, rows[entry_i]["t"], rows[i+1]["t"], entry_price)
            net = gross - round_trip_cost - funding
            equity *= max(0.000001, 1 + net)
            trades.append(net)
            if net > 0:
                wins += 1
                gross_win += net
            else:
                gross_loss += abs(net)
            peak = max(peak, equity)
            max_dd = max(max_dd, (peak-equity)/peak)
            entry_price = None
            i += 1
            continue
        i += 1
    if entry_price is not None and end > start + 1:
        exit_price = rows[end-1]["c"]
        funding = funding_cost(funding_events, rows[entry_i]["t"], rows[end-1]["t"], entry_price)
        net = exit_price / entry_price - 1 - round_trip_cost - funding
        equity *= max(0.000001, 1 + net)
        trades.append(net)
        if net > 0:
            wins += 1
            gross_win += net
        else:
            gross_loss += abs(net)
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak-equity)/peak)
    pf = gross_win / gross_loss if gross_loss else (999.0 if gross_win else 0.0)
    return {
        "trades": len(trades), "net_return_pct": round((equity-1)*100, 4),
        "max_drawdown_pct": round(max_dd*100, 4),
        "win_rate_pct": round(wins/len(trades)*100, 2) if trades else 0.0,
        "profit_factor": round(pf, 4),
        "positive_net": equity > 1 and pf > 1 and bool(trades)
    }


def benchmark(rows: list[dict], symbol: str, funding_events: list[dict] | None = None) -> dict:
    funding_events = funding_events or []
    # Chronological split: 60% train, next 20% validation, final 20% untouched holdout.
    n = len(rows)
    train_end, valid_end = int(n*0.6), int(n*0.8)
    results = {}
    for strategy in STRATEGIES:
        results[strategy] = {}
        for name, cost in COSTS.items():
            results[strategy][name] = {
                "train": simulate(rows, strategy, 201, train_end, cost, funding_events),
                "validation": simulate(rows, strategy, train_end, valid_end, cost, funding_events),
                "holdout": simulate(rows, strategy, valid_end, n, cost, funding_events)
            }
        holdout_pass = all(
            results[strategy][cost]["holdout"]["positive_net"]
            for cost in ("base", "stress", "severe")
        )
        all_splits_pass = all(
            results[strategy][cost][split]["positive_net"]
            for cost in ("base", "stress", "severe")
            for split in ("train", "validation", "holdout")
        )
        results[strategy]["gate"] = {
            "status": "PASS_RESEARCH_GATE" if all_splits_pass else "FAIL_RESEARCH_GATE",
            "holdout_only_status": "PASS_HOLDOUT_DIAGNOSTIC" if holdout_pass else "FAIL_HOLDOUT_DIAGNOSTIC",
            "production_approved": False,
            "rule": "Positive net return and PF>1 in train, validation, and holdout at base, 0.30%, and 0.46% round-trip costs. This is only a conservative research screen; passing does not approve production, and independent certification plus paper trading remain required."
        }
    return {"symbol": symbol.upper(), "interval": "1h", "bars": n,
            "strategy_scope": "Public strategy archetypes adapted to 1H candles; not direct reproduction of daily backtests.",
            "cost_assumptions_round_trip": COSTS,
            "split": {"train_end_index": train_end, "validation_end_index": valid_end, "holdout_start_index": valid_end},
            "funding_included": bool(funding_events),
            "funding_records": len(funding_events),
            "warning": "Long-only funding cashflows are estimated from supplied funding rates and mark prices; proxy mark prices are imperfect. Production approval remains false.",
            "results": results}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--funding-csv")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = benchmark(load_candles(args.csv), args.symbol, load_funding(args.funding_csv))
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(report, indent=2), encoding="utf-8")
    for name, result in report["results"].items():
        print(f"strategy={name} gate={result['gate']['status']}")
        if name in STRATEGIES:
            print(f"  holdout_base={result['base']['holdout']}")
            print(f"  holdout_stress={result['stress']['holdout']}")
            print(f"  holdout_severe={result['severe']['holdout']}")


if __name__ == "__main__":
    main()
