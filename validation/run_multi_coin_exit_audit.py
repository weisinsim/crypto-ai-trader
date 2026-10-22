#!/usr/bin/env python3
"""Run reproducible multi-coin 2.5R/3R exit audits over replayed signals."""
import argparse
import json
import math
from pathlib import Path

from validation.exit_parameter_audit import read_candles, read_signals, evaluate, summarize


def run_one(symbol, data_dir, signal_dir, costs, holdout_fraction, max_hold, min_holdout_trades=30):
    candles = read_candles(data_dir / f"{symbol}_1h.csv")
    signals = read_signals(signal_dir / f"{symbol}_signals.csv")
    if not candles:
        raise ValueError(f"No candles for {symbol}")
    matched = {c["ts"] for c in candles}
    split_ts = candles[int(len(candles) * (1 - holdout_fraction))]["ts"]
    # Split both candles and signals at the same chronological boundary. If
    # development signals are replayed against the full candle series, their
    # exits can consume holdout candles and leak future outcomes into development.
    periods = {
        "development": {
            "candles": [c for c in candles if c["ts"] < split_ts],
            "signals": [s for s in signals if s["ts"] < split_ts],
        },
        "holdout": {
            "candles": [c for c in candles if c["ts"] >= split_ts],
            "signals": [s for s in signals if s["ts"] >= split_ts],
        },
    }
    matched_signals = sum(s["ts"] in matched for s in signals)
    unmatched_signals = len(signals) - matched_signals
    split_index = int(len(candles) * (1 - holdout_fraction))
    result = {
        "symbol": symbol, "candles": len(candles), "signals": len(signals),
        "matched_signals": matched_signals,
        "unmatched_signals": unmatched_signals,
        "signal_match_rate": round(matched_signals / len(signals), 8) if signals else None,
        "evidence_quality_status": (
            "BLOCKED_UNMATCHED_SIGNALS" if unmatched_signals
            else "BLOCKED_NO_SIGNALS" if not signals
            else "RESEARCH_ONLY_MATCHED_SIGNALS"
        ),
        "split_timestamp_epoch": split_ts, "split_candle_index": split_index,
        "holdout_fraction": holdout_fraction,
        "max_hold_candles": max_hold, "cost_sensitivity_bps": costs,
        "periods": {}
    }
    for period, subset in periods.items():
        result["periods"][period] = {
            "candle_rows": len(subset["candles"]),
            "signal_rows": len(subset["signals"]),
        }
        for cost in costs:
            result["periods"][period][f"cost_{cost:g}_bps"] = {}
            for target_r in (2.5, 3.0):
                trades = evaluate(subset["candles"], subset["signals"],
                                  target_r, 2.0, cost, max_hold)
                scenario = summarize(trades)
                scenario["sample_status"] = (
                    "INSUFFICIENT_SAMPLE"
                    if period == "holdout" and scenario.get("trades", 0) < min_holdout_trades
                    else "SAMPLE_THRESHOLD_MET" if period == "holdout"
                    else "DEVELOPMENT_ONLY"
                )
                scenario["minimum_holdout_trades_required"] = min_holdout_trades
                result["periods"][period][f"cost_{cost:g}_bps"][f"target_{target_r:g}R"] = scenario
    holdout_scenarios = [
        scenario
        for cost_data in result["periods"]["holdout"].values()
        if isinstance(cost_data, dict)
        for scenario in cost_data.values()
        if isinstance(scenario, dict) and "trades" in scenario
    ]
    result["holdout_sample_gate"] = {
        "minimum_trades_required_per_scenario": min_holdout_trades,
        "scenarios_checked": len(holdout_scenarios),
        "scenarios_below_minimum": sum(s["trades"] < min_holdout_trades for s in holdout_scenarios),
        "all_scenarios_meet_minimum": bool(holdout_scenarios) and all(s["trades"] >= min_holdout_trades for s in holdout_scenarios),
    }
    return result


def aggregate_holdout_sample_gate(reports):
    """Aggregate per-coin holdout sample gates without treating empty evidence as a pass."""
    gates = [report.get("holdout_sample_gate", {}) for report in reports]
    checked = sum(int(gate.get("scenarios_checked", 0)) for gate in gates)
    below = sum(int(gate.get("scenarios_below_minimum", 0)) for gate in gates)
    failing_symbols = [
        report.get("symbol", "UNKNOWN")
        for report in reports
        if not report.get("holdout_sample_gate", {}).get("all_scenarios_meet_minimum", False)
    ]
    return {
        "coins_checked": len(reports),
        "scenarios_checked": checked,
        "scenarios_below_minimum": below,
        "coins_failing_sample_gate": failing_symbols,
        "all_coins_meet_minimum": bool(reports) and not failing_symbols,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-dir", default="data/historical")
    p.add_argument("--signal-dir", default="data/replay")
    p.add_argument("--out", default="data/replay/multi_coin_exit_audit.json")
    p.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT", "XRPUSDT"])
    p.add_argument("--cost-bps", nargs="+", type=float, default=[5, 10, 20, 30])
    p.add_argument("--holdout-fraction", type=float, default=0.30)
    p.add_argument("--max-hold", type=int, default=72)
    p.add_argument("--min-holdout-trades", type=int, default=30,
                   help="Minimum completed holdout trades required per cost/target scenario")
    a = p.parse_args()
    if (not a.cost_bps
            or any(not math.isfinite(c) or c < 0 for c in a.cost_bps)
            or len(set(a.cost_bps)) != len(a.cost_bps)
            or not math.isfinite(a.holdout_fraction)
            or not 0.05 <= a.holdout_fraction <= 0.45
            or a.max_hold < 1
            or a.min_holdout_trades < 1):
        p.error("Costs must be unique, finite, and non-negative; holdout fraction must be 0.05–0.45; max hold and minimum holdout trades must be positive")
    normalized_symbols = [symbol.strip().upper() for symbol in a.symbols]
    if any(not symbol for symbol in normalized_symbols):
        p.error("Symbols must be non-empty")
    if len(set(normalized_symbols)) != len(normalized_symbols):
        p.error("Duplicate symbols are not allowed; each asset must be audited once")
    reports = []
    for symbol in normalized_symbols:
        reports.append(run_one(symbol, Path(a.data_dir), Path(a.signal_dir),
                               a.cost_bps, a.holdout_fraction, a.max_hold,
                               a.min_holdout_trades))
    output = {
        "notice": "Research-only code replay and simplified OHLC exit simulation; not validated live performance or investment advice.",
        "promotion_status": "NOT_PROMOTED",
        "evidence_gate": {
            "all_symbols_have_matched_signals": all(r["evidence_quality_status"] == "RESEARCH_ONLY_MATCHED_SIGNALS" for r in reports),
            "holdout_sample_gate": aggregate_holdout_sample_gate(reports),
            "promotion_allowed": False,
            "reason": "Historical OHLC replay is research-only; live parity, exchange fills, funding settlement, and testnet lifecycle remain unverified."
        },
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
