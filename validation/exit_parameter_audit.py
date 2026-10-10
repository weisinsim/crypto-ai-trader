#!/usr/bin/env python3
"""Audit fixed entry signals under 2.5R vs 3R exits on OHLC candles.

candles.csv columns: timestamp,open,high,low,close
signals.csv columns: timestamp,side,entry,atr
Timestamps must match candle CLOSE timestamps exactly (the downloader emits UTC ISO-8601 close times). Each signal assumes entry
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
from datetime import datetime
from pathlib import Path


def parse_ts(value):
    value = value.strip()
    try:
        numeric = float(value)
        # Treat 13-digit Unix timestamps as milliseconds; 10-digit values are seconds.
        return int(numeric / 1000) if abs(numeric) >= 100_000_000_000 else int(numeric)
    except ValueError:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None or dt.utcoffset() is None:
            raise ValueError(f"Timestamp must include timezone: {value!r}")
        return dt.timestamp()


def read_candles(path):
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            c = {k: float(row[k]) for k in ("open", "high", "low", "close")}
            if (not all(math.isfinite(v) and v > 0 for v in c.values())
                    or c["low"] > min(c["open"], c["close"])
                    or c["high"] < max(c["open"], c["close"])
                    or c["low"] > c["high"]):
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
            if not math.isfinite(entry) or not math.isfinite(atr) or entry <= 0 or atr <= 0:
                raise ValueError("Entry and ATR must be finite and positive")
            stop = float(row["stop"]) if row.get("stop", "").strip() else None
            if stop is not None and (not math.isfinite(stop) or stop <= 0):
                raise ValueError("Stop must be finite and positive when supplied")
            rows.append({"ts": parse_ts(row["timestamp"]), "side": side, "entry": entry, "atr": atr, "stop": stop})
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
        entry = sig["entry"]
        d = 1 if sig["side"] == "LONG" else -1
        risk = abs(entry - sig["stop"]) if sig.get("stop") is not None else sig["atr"] * stop_atr
        if risk <= 0:
            continue
        stop = sig["stop"] if sig.get("stop") is not None else entry - d * risk
        # Ignore a supplied stop on the wrong side of entry; it indicates malformed signal data.
        if (d == 1 and stop >= entry) or (d == -1 and stop <= entry):
            raise ValueError(f"Stop is on wrong side of entry for signal at {sig['ts']}")
        target = entry + d * target_r * risk
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
        results.append({"timestamp": sig["ts"], "exit_timestamp": exit_ts, "side": sig["side"], "exit_reason": reason,
                        "gross_R": outcome, "cost_R": cost_r, "net_R": outcome - cost_r})
    return results


def summarize(trades):
    if not trades:
        return {"trades": 0, "win_rate_pct": None, "profit_factor": None,
                "net_R": 0.0, "avg_R": None, "max_drawdown_R": None}
    # Drawdown must follow chronological trade order, not an incidental caller order.
    chronological = sorted(trades, key=lambda t: t.get("timestamp", 0))
    vals = [t["net_R"] for t in chronological]
    gross_profit = sum(x for x in vals if x > 0)
    gross_loss = -sum(x for x in vals if x < 0)
    equity = peak = dd = 0.0
    for x in vals:
        equity += x
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
    # This is signal-level research, not a sequential single-position portfolio.
    timed_trades = [t for t in trades if "timestamp" in t]
    ordered = sorted(timed_trades, key=lambda t: t["timestamp"])
    overlap_count = 0
    for i, trade in enumerate(ordered):
        end_ts = trade.get("exit_timestamp", trade["timestamp"])
        if any(other["timestamp"] < end_ts and other.get("exit_timestamp", other["timestamp"]) > trade["timestamp"]
               for j, other in enumerate(ordered) if i != j):
            overlap_count += 1
    events = []
    for trade in timed_trades:
        events.append((trade["timestamp"], 1))
        events.append((trade.get("exit_timestamp", trade["timestamp"]), -1))
    events.sort(key=lambda event: (event[0], event[1]))
    active = max_active = 0
    for _, delta in events:
        active += delta
        max_active = max(max_active, active)
    pf = gross_profit / gross_loss if gross_loss else (None if gross_profit == 0 else "Infinity")
    return {
        "trades": len(vals),
        "win_rate_pct": round(100 * sum(x > 0 for x in vals) / len(vals), 2),
        "profit_factor": round(pf, 4) if isinstance(pf, float) else pf,
        "net_R": round(sum(vals), 4),
        "avg_R": round(sum(vals) / len(vals), 4),
        "max_drawdown_R": round(dd, 4),
        "overlapping_trade_count": overlap_count,
        "max_concurrent_trades": max_active,
        "tp_count": sum(t["exit_reason"] == "TP" for t in trades),
        "sl_count": sum(t["exit_reason"] == "SL" for t in trades),
        "time_exit_count": sum(t["exit_reason"] == "TIME" for t in trades),
    }


def filter_single_position(trades):
    """Keep the first signal only when no prior simulated trade remains open.

    Uses exit timestamps from the OHLC replay. This is a conservative, single-position
    comparison, not a multi-asset portfolio simulator or a fill/fee engine.
    """
    ordered = sorted(trades, key=lambda t: t.get("timestamp", 0))
    accepted = []
    active_until = None
    skipped = 0
    for trade in ordered:
        entry_ts = trade.get("timestamp")
        exit_ts = trade.get("exit_timestamp", entry_ts)
        if entry_ts is None or exit_ts is None:
            raise ValueError("Single-position filtering requires timestamp and exit_timestamp")
        if active_until is not None and entry_ts < active_until:
            skipped += 1
            continue
        accepted.append(trade)
        active_until = exit_ts
    return accepted, skipped


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--candles", required=True)
    p.add_argument("--signals", required=True)
    p.add_argument("--stop-atr", type=float, default=2.0)
    p.add_argument("--cost-bps", type=float, default=10.0)
    p.add_argument("--max-hold", type=int, default=72)
    p.add_argument("--holdout-fraction", type=float, default=0.30)
    p.add_argument("--min-match-pct", type=float, default=95.0, help="Abort if fewer than this percent of signals match candle timestamps")
    p.add_argument("--out", default="exit_audit_results.json")
    a = p.parse_args()
    if a.stop_atr <= 0 or a.cost_bps < 0 or a.max_hold < 1 or not 0.05 <= a.holdout_fraction <= 0.45 or not 0 <= a.min_match_pct <= 100:
        p.error("Invalid parameters: stop-atr > 0, cost-bps >= 0, max-hold >= 1, holdout fraction 0.05–0.45, match threshold 0–100")
    candles, signals = read_candles(a.candles), read_signals(a.signals)
    if not signals:
        raise SystemExit("No signals found. No backtest was performed.")
    split_i = int(len(candles) * (1 - a.holdout_fraction))
    candle_ts = {c["ts"] for c in candles}
    matched_count = sum(s["ts"] in candle_ts for s in signals)
    unmatched_count = len(signals) - matched_count
    if matched_count == 0:
        raise SystemExit("No signal timestamps match candle timestamps; check timestamp convention and timezone. No backtest was performed.")
    match_pct = 100 * matched_count / len(signals)
    if match_pct < a.min_match_pct:
        raise SystemExit(f"Only {match_pct:.2f}% of signals match candle timestamps; minimum is {a.min_match_pct:.2f}%. No backtest was performed.")
    split_ts = candles[split_i]["ts"]
    periods = {
        "development": {"candles": candles[:split_i], "signals": [s for s in signals if s["ts"] < split_ts]},
        "holdout": {"candles": candles[split_i:], "signals": [s for s in signals if s["ts"] >= split_ts]},
    }
    output = {
        "notice": "Exit comparison only; not a validated strategy or investment recommendation.",
        "inputs": {"candle_rows": len(candles), "signal_rows": len(signals),
                   "matched_signal_rows": matched_count,
                   "unmatched_signal_rows": unmatched_count,
                   "signal_timestamp_match_pct": round(match_pct, 2),
                   "minimum_required_match_pct": a.min_match_pct,
                   "holdout_start_epoch": split_ts, "stop_atr_fallback": a.stop_atr,
                   "uses_signal_stop_when_present": True,
                   "round_trip_cost_bps": a.cost_bps, "max_hold_candles": a.max_hold},
        "periods": {}
    }
    for name, subset in periods.items():
        output["periods"][name] = {}
        output["periods"][name]["signal_rows"] = len(subset["signals"])
        for r in (2.5, 3.0):
            trades = evaluate(subset["candles"], subset["signals"], r, a.stop_atr, a.cost_bps, a.max_hold)
            sequential, skipped = filter_single_position(trades)
            output["periods"][name][f"target_{r}R"] = {
                "signal_level": summarize(trades),
                "single_position": summarize(sequential),
                "single_position_skipped_overlapping_signals": skipped,
            }
    Path(a.out).write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
