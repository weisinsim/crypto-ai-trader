#!/usr/bin/env python3
"""Run reproducible exit comparisons for replayed signals across multiple assets.

This runner reports research metrics only. It does not declare a profitable model,
and it will mark results as insufficient when the signal sample is too small.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT", "XRPUSDT"]
COSTS = [5, 10, 20, 30]


def holdout_max_trade_count(holdout):
    return max((v.get("trades", 0) for v in holdout.values() if isinstance(v, dict)), default=0)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-dir", default="data/historical")
    p.add_argument("--signals-dir", default="data/replay")
    p.add_argument("--out-dir", default="data/exit_audits")
    p.add_argument("--symbols", nargs="+", default=SYMBOLS)
    p.add_argument("--max-hold", type=int, default=72)
    p.add_argument("--holdout-fraction", type=float, default=0.30)
    p.add_argument("--min-trades", type=int, default=30)
    args = p.parse_args()
    root = Path(__file__).resolve().parents[1]
    data, signals, out = Path(args.data_dir), Path(args.signals_dir), Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    reports = []
    for symbol in args.symbols:
        candle_file = data / f"{symbol}_1h.csv"
        signal_file = signals / f"{symbol}_signals.csv"
        if not candle_file.exists() or not signal_file.exists():
            reports.append({"symbol": symbol, "status": "INSUFFICIENT_DATA",
                            "missing": [str(x) for x in (candle_file, signal_file) if not x.exists()]})
            continue
        for cost in COSTS:
            dest = out / f"{symbol}_cost_{cost}bps.json"
            cmd = [
                sys.executable, str(root / "validation" / "exit_parameter_audit.py"),
                "--candles", str(candle_file), "--signals", str(signal_file),
                "--cost-bps", str(cost), "--max-hold", str(args.max_hold),
                "--holdout-fraction", str(args.holdout_fraction), "--out", str(dest),
            ]
            run = subprocess.run(cmd, capture_output=True, text=True)
            if run.returncode != 0 or not dest.exists():
                reports.append({"symbol": symbol, "cost_bps": cost, "status": "AUDIT_FAILED",
                                "returncode": run.returncode, "stderr": run.stderr[-1500:]})
                continue
            result = json.loads(dest.read_text(encoding="utf-8"))
            holdout = result.get("periods", {}).get("holdout", {})
            trades = holdout_max_trade_count(holdout)
            reports.append({
                "symbol": symbol, "cost_bps": cost,
                "status": "RESEARCH_ONLY" if trades >= args.min_trades else "INSUFFICIENT_SAMPLE",
                "holdout_max_trade_count_across_exit_variants": trades,
                "audit_file": str(dest),
                "warning": "Do not select a model from a single cost assumption; inspect both exit variants and all metrics."
            })
    summary = {
        "notice": "Automated batch runner. No profitability claim. Check audit files, data integrity, parity, funding, slippage and independent holdout.",
        "min_trades_threshold": args.min_trades,
        "cost_sensitivity_bps": COSTS,
        "reports": reports,
    }
    dest = out / "batch_manifest.json"
    dest.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
