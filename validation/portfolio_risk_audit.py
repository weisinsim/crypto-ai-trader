#!/usr/bin/env python3
"""Audit overlapping multi-symbol trade risk from a JSON trade list.

Input is a JSON array of objects with symbol, timestamp (entry epoch seconds),
exit_timestamp (epoch seconds), and risk_pct (percent of account equity lost
at the planned stop, e.g. 0.5 means 0.5%). This is a conservative exposure
audit, not a correlation model or a guarantee of stop execution.
"""
import argparse
import json
import math
from collections import defaultdict
from pathlib import Path


def audit_portfolio_risk(trades, max_portfolio_risk_pct=2.0, max_symbol_risk_pct=1.0):
    for name, value in (("max_portfolio_risk_pct", max_portfolio_risk_pct),
                        ("max_symbol_risk_pct", max_symbol_risk_pct)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be a finite positive number")
    normalized = []
    seen = set()
    for i, trade in enumerate(trades):
        symbol = str(trade.get("symbol", "")).strip().upper()
        entry_ts, exit_ts, risk_pct = trade.get("timestamp"), trade.get("exit_timestamp"), trade.get("risk_pct")
        if not symbol:
            raise ValueError(f"Trade {i} is missing symbol")
        if isinstance(entry_ts, bool) or not isinstance(entry_ts, (int, float)) or not math.isfinite(entry_ts):
            raise ValueError(f"Trade {i} has invalid timestamp")
        if isinstance(exit_ts, bool) or not isinstance(exit_ts, (int, float)) or not math.isfinite(exit_ts):
            raise ValueError(f"Trade {i} has invalid exit_timestamp")
        if exit_ts <= entry_ts:
            raise ValueError(f"Trade {i} exit_timestamp must be strictly after entry timestamp")
        if isinstance(risk_pct, bool) or not isinstance(risk_pct, (int, float)) or not math.isfinite(risk_pct) or risk_pct <= 0:
            raise ValueError(f"Trade {i} risk_pct must be finite and positive")
        trade_id = str(trade.get("trade_id", i))
        if trade_id in seen:
            raise ValueError(f"Duplicate trade_id {trade_id!r}")
        seen.add(trade_id)
        normalized.append({"trade_id": trade_id, "symbol": symbol, "timestamp": entry_ts,
                           "exit_timestamp": exit_ts, "risk_pct": float(risk_pct)})

    # Exit events are processed before entries at the same timestamp, so a
    # closed position does not overlap a new position opened at that instant.
    events = []
    for trade in normalized:
        events.append((trade["timestamp"], 1, trade))
        events.append((trade["exit_timestamp"], 0, trade))
    events.sort(key=lambda event: (event[0], event[1]))
    active = {}
    symbol_risk = defaultdict(float)
    total_risk = 0.0
    peak_total = 0.0
    peak_positions = 0
    peak_symbol_risk = defaultdict(float)
    breaches = []
    for ts, kind, trade in events:
        tid = trade["trade_id"]
        if kind == 0:
            if tid in active:
                current = active.pop(tid)
                total_risk -= current["risk_pct"]
                symbol_risk[current["symbol"]] -= current["risk_pct"]
                if abs(total_risk) < 1e-12:
                    total_risk = 0.0
                if abs(symbol_risk[current["symbol"]]) < 1e-12:
                    symbol_risk[current["symbol"]] = 0.0
            continue
        active[tid] = trade
        total_risk += trade["risk_pct"]
        symbol_risk[trade["symbol"]] += trade["risk_pct"]
        peak_total = max(peak_total, total_risk)
        peak_positions = max(peak_positions, len(active))
        for symbol, value in symbol_risk.items():
            peak_symbol_risk[symbol] = max(peak_symbol_risk[symbol], value)
        if total_risk > max_portfolio_risk_pct + 1e-12:
            breaches.append({"timestamp": ts, "type": "portfolio_risk",
                             "actual_risk_pct": round(total_risk, 8),
                             "limit_risk_pct": max_portfolio_risk_pct,
                             "active_trade_ids": sorted(active)})
        if symbol_risk[trade["symbol"]] > max_symbol_risk_pct + 1e-12:
            breaches.append({"timestamp": ts, "type": "symbol_risk", "symbol": trade["symbol"],
                             "actual_risk_pct": round(symbol_risk[trade["symbol"]], 8),
                             "limit_risk_pct": max_symbol_risk_pct,
                             "active_trade_ids": sorted(t for t, item in active.items()
                                                         if item["symbol"] == trade["symbol"])})
    return {
        "trade_count": len(normalized),
        "max_concurrent_positions": peak_positions,
        "max_concurrent_stop_risk_pct": round(peak_total, 8),
        "max_concurrent_stop_risk_by_symbol_pct": {
            symbol: round(value, 8) for symbol, value in sorted(peak_symbol_risk.items())
        },
        "portfolio_risk_limit_pct": max_portfolio_risk_pct,
        "per_symbol_risk_limit_pct": max_symbol_risk_pct,
        "breach_count": len(breaches),
        "breaches": breaches,
        "notice": "Stop-risk estimates do not include gap risk, slippage, liquidation, or correlation shocks."
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trades", required=True, help="JSON file containing a list of trade records")
    parser.add_argument("--max-portfolio-risk-pct", type=float, default=2.0)
    parser.add_argument("--max-symbol-risk-pct", type=float, default=1.0)
    parser.add_argument("--out", default="portfolio_risk_audit.json")
    args = parser.parse_args()
    trades = json.loads(Path(args.trades).read_text(encoding="utf-8"))
    if not isinstance(trades, list):
        raise SystemExit("Trade input must be a JSON array; no audit performed.")
    result = audit_portfolio_risk(trades, args.max_portfolio_risk_pct, args.max_symbol_risk_pct)
    Path(args.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
