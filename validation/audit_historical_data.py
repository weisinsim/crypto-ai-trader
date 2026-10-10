#!/usr/bin/env python3
"""Audit historical candle CSV completeness before replay/backtesting."""
import argparse
import csv
import json
import math
from datetime import datetime
from pathlib import Path

INTERVAL_MS = {"15m": 900_000, "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "1d": 86_400_000}


def parse_ts(v):
    dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(f"Timestamp must include timezone: {v!r}")
    return int(dt.timestamp() * 1000)


def _valid_timestamp(value):
    try:
        parse_ts(value)
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def audit(path, interval):
    if interval not in INTERVAL_MS:
        raise ValueError(f"Unsupported interval: {interval}")
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    try:
        ts = [parse_ts(r["timestamp"]) for r in rows]
        timestamp_errors = 0
    except (ValueError, TypeError, KeyError):
        timestamp_errors = sum(1 for r in rows if not _valid_timestamp(r.get("timestamp")))
        ts = []
    if timestamp_errors:
        return {"file": str(path), "rows": len(rows), "timestamp_errors": timestamp_errors,
                "pass_basic_integrity": False, "error": "INVALID_OR_TIMEZONE_NAIVE_TIMESTAMP"}
    if not rows:
        return {"file": str(path), "rows": 0, "timestamp_errors": 0,
                "pass_basic_integrity": False, "error": "EMPTY_FILE"}
    duplicates = len(ts) - len(set(ts))
    non_increasing = sum(1 for a, b in zip(ts, ts[1:]) if b <= a)
    gaps = []
    cadence_anomalies = []
    step = INTERVAL_MS[interval]
    for a, b in zip(ts, ts[1:]):
        delta = b - a
        if delta > step:
            gaps.append({"after": a, "before": b, "missing_approx": max(0, delta//step-1)})
        if delta != step:
            cadence_anomalies.append({"after": a, "before": b, "delta_ms": delta})
    invalid_ohlc = 0
    for r in rows:
        try:
            o, h, low, close = (float(r[k]) for k in ("open", "high", "low", "close"))
            valid = all(math.isfinite(v) and v > 0 for v in (o, h, low, close))
            valid = valid and low <= min(o, close) and h >= max(o, close) and low <= h
        except (ValueError, TypeError, KeyError):
            valid = False
        if not valid:
            invalid_ohlc += 1
    return {"file": str(path), "rows": len(rows), "first_close_ms": ts[0] if ts else None,
            "last_close_ms": ts[-1] if ts else None, "duplicates": duplicates,
            "non_increasing_pairs": non_increasing, "gap_count": len(gaps),
            "cadence_anomaly_count": len(cadence_anomalies), "cadence_anomalies_sample": cadence_anomalies[:10],
            "missing_candles_approx": sum(g["missing_approx"] for g in gaps),
            "invalid_ohlc_rows": invalid_ohlc, "gaps_sample": gaps[:10], "timestamp_errors": timestamp_errors,
            "pass_basic_integrity": bool(rows) and duplicates == 0 and non_increasing == 0 and invalid_ohlc == 0 and not cadence_anomalies}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-dir", default="data/historical")
    p.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT", "XRPUSDT"])
    args = p.parse_args()
    root = Path(args.data_dir)
    report = []
    for symbol in args.symbols:
        for interval in ("1h", "4h"):
            path = root / f"{symbol}_{interval}.csv"
            if not path.exists():
                report.append({"file": str(path), "error": "MISSING_FILE", "pass_basic_integrity": False})
            else:
                report.append(audit(path, interval))
    print(json.dumps({"notice": "A basic integrity pass does not establish gap-free history or strategy profitability.", "files": report,
                      "all_basic_integrity_pass": all(x.get("pass_basic_integrity", False) for x in report)}, indent=2))


if __name__ == "__main__":
    main()
