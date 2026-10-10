#!/usr/bin/env python3
"""Run reproducible exit comparisons for replayed signals across multiple assets.

This runner reports research metrics only. It does not declare a profitable model,
and it will mark results as insufficient when the signal sample is too small.
"""
import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

from validation.audit_historical_data import audit as audit_candles

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT", "XRPUSDT"]
COSTS = [5, 10, 20, 30]


def holdout_trade_counts(holdout):
    # Read both legacy flat metrics and current nested signal/single-position metrics.
    variants = []
    for value in holdout.values():
        if not isinstance(value, dict):
            continue
        if "signal_level" in value or "single_position" in value:
            for mode in ("signal_level", "single_position"):
                metrics = value.get(mode)
                if isinstance(metrics, dict) and "trades" in metrics:
                    variants.append(metrics["trades"])
        elif "trades" in value:
            variants.append(value["trades"])
    return variants


def holdout_min_trade_count(holdout):
    counts = holdout_trade_counts(holdout)
    return min(counts) if counts else 0


def holdout_max_trade_count(holdout):
    counts = holdout_trade_counts(holdout)
    return max(counts) if counts else 0


def check_candle_integrity(candle_file, four_hour_file):
    integrity = {
        "1h": audit_candles(candle_file, "1h"),
        "4h": audit_candles(four_hour_file, "4h"),
    }
    failed = [interval for interval, result in integrity.items()
              if not result.get("pass_basic_integrity", False)]
    return integrity, failed


def check_timeframe_alignment(candle_file, four_hour_file):
    """Require every 4h close timestamp to exist in the 1h series."""
    def timestamps(path):
        with open(path, newline="", encoding="utf-8-sig") as stream:
            return {parse_timestamp(row["timestamp"]) for row in csv.DictReader(stream)}

    hourly = timestamps(candle_file)
    four_hourly = timestamps(four_hour_file)
    missing = sorted(four_hourly - hourly)
    return {
        "pass": bool(hourly and four_hourly) and not missing,
        "hourly_rows": len(hourly),
        "four_hour_rows": len(four_hourly),
        "four_hour_closes_missing_from_1h": len(missing),
        "missing_examples_ms": missing[:10],
    }


def parse_timestamp(value):
    from datetime import datetime
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(f"Timestamp must include timezone: {value!r}")
    return int(dt.timestamp() * 1000)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-dir", default="data/historical")
    p.add_argument("--signals-dir", default="data/replay")
    p.add_argument("--funding-dir", default="data/funding",
                   help="Directory with SYMBOL_funding.csv files")
    p.add_argument("--allow-missing-funding", action="store_true",
                   help="Allow research-only audits without historical funding; results are not funding-adjusted")
    p.add_argument("--out-dir", default="data/exit_audits")
    p.add_argument("--symbols", nargs="+", default=SYMBOLS)
    p.add_argument("--max-hold", type=int, default=72)
    p.add_argument("--holdout-fraction", type=float, default=0.30)
    p.add_argument("--min-trades", type=int, default=30)
    args = p.parse_args()
    if args.max_hold < 1:
        p.error("--max-hold must be >= 1")
    if not 0.05 <= args.holdout_fraction <= 0.45:
        p.error("--holdout-fraction must be between 0.05 and 0.45")
    if args.min_trades < 1:
        p.error("--min-trades must be >= 1")
    if not args.symbols or any(not symbol.strip() for symbol in args.symbols):
        p.error("--symbols must contain at least one non-empty symbol")
    root = Path(__file__).resolve().parents[1]
    data, signals, out = Path(args.data_dir), Path(args.signals_dir), Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    reports = []
    for symbol in args.symbols:
        candle_file = data / f"{symbol}_1h.csv"
        four_hour_file = data / f"{symbol}_4h.csv"
        signal_file = signals / f"{symbol}_signals.csv"
        funding_file = Path(args.funding_dir) / f"{symbol}_funding.csv"
        missing = [str(x) for x in (candle_file, four_hour_file, signal_file) if not x.exists()]
        if not args.allow_missing_funding and not funding_file.exists():
            missing.append(str(funding_file))
        if missing:
            reports.append({"symbol": symbol, "status": "INSUFFICIENT_DATA", "missing": missing})
            continue
        integrity, failed_intervals = check_candle_integrity(candle_file, four_hour_file)
        # Do not parse alignment timestamps after the integrity gate has already
        # found malformed/naive timestamps; report the failure instead of aborting
        # the entire multi-asset batch.
        alignment = (check_timeframe_alignment(candle_file, four_hour_file)
                     if not failed_intervals else {"pass": False, "skipped": True,
                     "reason": "Candle integrity failed; timestamp alignment was not evaluated."})
        if failed_intervals or not alignment["pass"]:
            reports.append({
                "symbol": symbol, "status": "DATA_INTEGRITY_FAILED",
                "failed_intervals": failed_intervals,
                "integrity": integrity,
                "timeframe_alignment": alignment,
                "notice": "No exit audit was run for this symbol because candle integrity or 1h/4h timestamp alignment failed."
            })
            continue
        for cost in COSTS:
            dest = out / f"{symbol}_cost_{cost}bps.json"
            cmd = [
                sys.executable, str(root / "validation" / "exit_parameter_audit.py"),
                "--candles", str(candle_file), "--signals", str(signal_file),
                "--cost-bps", str(cost), "--max-hold", str(args.max_hold),
                "--holdout-fraction", str(args.holdout_fraction), "--out", str(dest),
            ]
            if funding_file.exists():
                cmd.extend(["--funding-file", str(funding_file)])
            run = subprocess.run(cmd, capture_output=True, text=True)
            if run.returncode != 0 or not dest.exists():
                reports.append({"symbol": symbol, "cost_bps": cost, "status": "AUDIT_FAILED",
                                "returncode": run.returncode, "stderr": run.stderr[-1500:]})
                continue
            result = json.loads(dest.read_text(encoding="utf-8"))
            # Verify the child audit actually used the expected funding file.
            # In required mode, never treat an unadjusted audit as a valid result.
            used_funding_file = result.get("inputs", {}).get("funding_file")
            if not args.allow_missing_funding and not used_funding_file:
                reports.append({
                    "symbol": symbol, "cost_bps": cost, "status": "AUDIT_FAILED",
                    "reason": "Funding was required but the child audit did not record a funding file.",
                    "audit_file": str(dest),
                })
                continue
            holdout = result.get("periods", {}).get("holdout", {})
            trades = holdout_max_trade_count(holdout)
            min_variant_trades = holdout_min_trade_count(holdout)
            inputs = result.get("inputs", {})
            variant_metrics = {
                key: value for key, value in holdout.items()
                if isinstance(value, dict) and ("signal_level" in value or "single_position" in value)
            }
            reports.append({
                "symbol": symbol, "cost_bps": cost,
                "status": "RESEARCH_ONLY" if min_variant_trades >= args.min_trades else "INSUFFICIENT_SAMPLE",
                "holdout_min_trade_count_across_exit_variants": min_variant_trades,
                "holdout_max_trade_count_across_exit_variants": trades,
                "signal_timestamp_match_pct": inputs.get("signal_timestamp_match_pct"),
                "candle_integrity": {k: v.get("pass_basic_integrity") for k, v in integrity.items()},
                "holdout_metrics_by_exit": variant_metrics,
                "audit_file": str(dest),
                "funding_history_used": bool(used_funding_file),
                "funding_file_recorded_by_audit": used_funding_file,
                "warning": "Research output only; inspect both exit variants, all cost assumptions, parity, and data quality. Funding-adjusted conclusions require verified complete funding history."
            })
    summary = {
        "notice": "Automated batch runner. No profitability claim. Check audit files, data integrity, parity, funding, slippage and independent holdout.",
        "min_trades_threshold": args.min_trades,
        "historical_funding_required": not args.allow_missing_funding,
        "cost_sensitivity_bps": COSTS,
        "reports": reports,
    }
    dest = out / "batch_manifest.json"
    dest.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
