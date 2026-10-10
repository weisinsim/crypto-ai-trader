#!/usr/bin/env python3
"""Run reproducible multi-coin 2.5R/3R exit audits over replayed signals."""
import argparse
import json
from pathlib import Path

from validation.exit_parameter_audit import read_candles, read_signals, evaluate, summarize


def run_one(symbol, data_dir, signal_dir, costs, holdout_fraction, max_hold):
    candles = read_candles(data_dir / f"{symbol}_1h.csv")
    signals = read_signals(signal_dir / f"{symbol}_signals.csv")
    if not candles:
        raise ValueError(f"No candles for {symbol}")
    matched = {c["ts"] for c in candles}
    split_ts = candles[int(len(candles) * (1 - holdout_fraction))]["ts"]
    periods = {
        "development": [s for s in signals if s["ts"] < split_ts],
        "holdout": [s for s in signals if s["ts"] >= split_ts],
    }
    result = {
        "symbol": symbol, "candles": len(candles), "signals": len(signals),
        "matched_signals": sum(s["ts"] in matched for s in signals),
        "unmatched_signals": sum(s["ts"] not in matched for s in signals),
        "split_timestamp_epoch": split_ts, "holdout_fraction": holdout_fraction,
        "max_hold_candles": max_hold, "cost_sensitivity_bps": costs,
        "periods": {}
    }
    for period, subset in periods.items():
        result["periods"][period] = {}
        for cost in costs:
            result["periods"][period][f"cost_{cost:g}_bps"] = {}
            for target_r in (2.5, 3.0):
                trades = evaluate(candles, subset, target_r, 2.0, cost, max_hold)
                result["periods"][period][f"cost_{cost:g}_bps"][f"target_{target_r:g}R"] = summarize(trades)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-dir", default="data/historical")
    p.add_argument("--signal-dir", default="data/replay")
    p.add_argument("--out", default="data/replay/multi_coin_exit_audit.json")
    p.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT", "XRPUSDT"])
    p.add_argument("--cost-bps", nargs="+", type=float, default=[5, 10, 20, 30])
    p.add_argument("--holdout-fraction", type=float, default=0.30)
    p.add_argument("--max-hold", type=int, default=72)
    a = p.parse_args()
    if any(c < 0 for c in a.cost_bps) or not 0.05 <= a.holdout_fraction <= 0.45 or a.max_hold < 1:
        p.error("Invalid costs, holdout fraction (0.05–0.45), or max hold")
    reports = []
    for symbol in a.symbols:
        reports.append(run_one(symbol, Path(a.data_dir), Path(a.signal_dir),
                               a.cost_bps, a.holdout_fraction, a.max_hold))
    output = {
        "notice": "Research-only code replay and simplified OHLC exit simulation; not validated live performance or investment advice.",
        "promotion_status": "NOT_PROMOTED",
        "assumptions": {"targets_R": [2.5, 3.0], "stop": "signal-specific stop when present",
                        "same_candle_collision": "stop-first", "costs": "round-trip bps of entry notional",
                        "funding_and_partial_exits": "not modeled"},
        "coins": reports
    }
    dest = Path(a.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(dest), "coins": len(reports),
                      "matched_signals": {r["symbol"]: r["matched_signals"] for r in reports},
                      "notice": output["notice"]}, indent=2))


if __name__ == "__main__":
    main()
