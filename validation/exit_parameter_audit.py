#!/usr/bin/env python3
"""Audit fixed entry signals under 2.5R vs 3R exits on OHLC candles.

candles.csv columns: timestamp,open,high,low,close
signals.csv columns: timestamp,side,entry,atr
Timestamps must match candle OPEN timestamps exactly. Each signal assumes entry
at the recorded price after that signal candle closes and is evaluated from the NEXT candle onward. Same-candle stop and
target collisions are resolved as stop-first. Costs are round-trip bps of entry
notional, converted to R. The final holdout is chronological, not shuffled.

This evaluates exit parameters only. It does not validate signal generation,
funding, realistic fills, overlapping positions, or portfolio-level risk.
"""
import argparse
import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path


def parse_ts(value):
    value = value.strip()
    try:
        numeric = float(value)
        # Treat 13-digit Unix timestamps as milliseconds; 10-digit values are seconds.
        return int(numeric / 1000) if abs(numeric) >= 100_000_000_000 else int(numeric)
    except ValueError:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError(f"Timestamp must include timezone: {value!r}")
        return dt.timestamp()


def read_candles(path):
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            c = {k: float(row[k]) for k in ("open", "high", "low", "close")}
            if c["low"] > min(c["open"], c["close"]) or c["high"] < max(c["open"], c["close"]) or c["low"] > c["high"]:
                raise ValueError(f"Invalid OHLC values at {row['timestamp']}")
            rows.append({"ts": parse_ts(row["timestamp"]), **c})
    rows.sort(key=lambda x: x["ts"])
    if len(rows) < 3:
        raise ValueError("At least 3 candles are required")
    if len({r["ts"] for r in rows}) != len(rows):
        raise ValueError("Duplicate candle timestamps")
    return rows


def read_signals(path):
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            side = row["side"].strip().upper()
            if side not in ("LONG", "SHORT"):
                raise ValueError(f"Invalid side {side!r}; expected LONG or SHORT")
            entry, atr = float(row["entry"]), float(row["atr"])
            if entry <= 0 or atr <= 0:
                raise ValueError("Entry and ATR must be positive")
            rows.append({"ts": parse_ts(row["timestamp"]), "side": side, "entry": entry, "atr": atr})
    rows.sort(key=lambda x: x["ts"])
    if len({r["ts"] for r in rows}) != len(rows):
        raise ValueError("Duplicate signal timestamps; one signal per timestamp is supported")
    return rows


def evaluate(candles, signals, target_r, stop_atr, cost_bps, max_hold):
    index = {c["ts"]: i for i, c in enumerate(candles)}
    results = []
    for sig in signals:
        i = index.get(sig["ts"])
        if i is None:
            continue
        entry, risk = sig["entry"], sig["atr"] * stop_atr
        d = 1 if sig["side"] == "LONG" else -1
        stop, target = entry - d * risk, entry + d * target_r * risk
        future = candles[i + 1:i + 1 + max_hold]
        if not future:
            continue
        outcome, reason, exit_ts = None, "TIME", future[-1]["ts"]
        for candle in future:
            stop_hit = candle["low"] <= stop if d == 1 else candle["high"] >= stop
            target_hit = candle["high"] >= target if d == 1 else candle["low"] <= target
            if stop_hit:  # conservative when both levels are touched in one candle
                outcome, reason, exit_ts = -1.0, "SL", candle["ts"]
                break
            if target_hit:
                outcome, reason, exit_ts = target_r, "TP", candle["ts"]
                break
        if outcome is None:
            close = next(c["close"] for c in future if c["ts"] == exit_ts)
            outcome = d * (close - entry) / risk
        cost_r = (cost_bps / 10000.0) * entry / risk
        results.append({"timestamp": sig["ts"], "side": sig["side"], "exit_reason": reason,
                        "gross_R": outcome, "cost_R": cost_r, "net_R": outcome - cost_r})
    return results


def summarize(trades):
    if not trades:
        return {"trades": 0, "win_rate_pct": None, "profit_factor": None,
                "net_R": 0.0, "avg_R": None, "max_drawdown_R": None}
    vals = [t["net_R"] for t in trades]
    gross_profit = sum(x for x in vals if x > 0)
    gross_loss = -sum(x for x in vals if x < 0)
    equity = peak = dd = 0.0
    for x in vals:
        equity += x
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
    pf = gross_profit / gross_loss if gross_loss else (None if gross_profit == 0 else "Infinity")
    return {
        "trades": len(vals),
        "win_rate_pct": round(100 * sum(x > 0 for x in vals) / len(vals), 2),
        "profit_factor": round(pf, 4) if isinstance(pf, float) else pf,
        "net_R": round(sum(vals), 4),
        "avg_R": round(sum(vals) / len(vals), 4),
        "max_drawdown_R": round(dd, 4),
        "tp_count": sum(t["exit_reason"] == "TP" for t in trades),
        "sl_count": sum(t["exit_reason"] == "SL" for t in trades),
        "time_exit_count": sum(t["exit_reason"] == "TIME" for t in trades),
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--candles", required=True)
    p.add_argument("--signals", required=True)
    p.add_argument("--stop-atr", type=float, default=2.0)
    p.add_argument("--cost-bps", type=float, default=10.0)
    p.add_argument("--max-hold", type=int, default=72)
    p.add_argument("--holdout-fraction", type=float, default=0.30)
    p.add_argument("--out", default="exit_audit_results.json")
    a = p.parse_args()
    if a.stop_atr <= 0 or a.cost_bps < 0 or a.max_hold < 1 or not 0.05 <= a.holdout_fraction <= 0.45:
        p.error("Invalid parameters: stop-atr > 0, cost-bps >= 0, max-hold >= 1, holdout fraction 0.05–0.45")
    candles, signals = read_candles(a.candles), read_signals(a.signals)
    if not signals:
        raise SystemExit("No signals found. No backtest was performed.")
    split_i = int(len(candles) * (1 - a.holdout_fraction))
    split_ts = candles[split_i]["ts"]
    periods = {"development": [s for s in signals if s["ts"] < split_ts],
               "holdout": [s for s in signals if s["ts"] >= split_ts]}
    output = {
        "notice": "Exit comparison only; not a validated strategy or investment recommendation.",
        "inputs": {"candle_rows": len(candles), "signal_rows": len(signals),
                   "matched_signal_rows": sum(s["ts"] in {c["ts"] for c in candles} for s in signals),
                   "holdout_start_epoch": split_ts, "stop_atr": a.stop_atr,
                   "round_trip_cost_bps": a.cost_bps, "max_hold_candles": a.max_hold},
        "periods": {}
    }
    for name, subset in periods.items():
        output["periods"][name] = {}
        for r in (2.5, 3.0):
            trades = evaluate(candles, subset, r, a.stop_atr, a.cost_bps, a.max_hold)
            output["periods"][name][f"target_{r}R"] = summarize(trades)
    Path(a.out).write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
