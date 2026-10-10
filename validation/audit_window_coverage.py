#!/usr/bin/env python3
"""Check candle files cover the requested UTC [start, end) window exactly."""
import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

INTERVALS = {"1h": 3_600_000, "4h": 14_400_000}
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT", "XRPUSDT"]


def utc_ms(value):
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    elif dt.utcoffset() is None:
        raise ValueError(f"Invalid timezone: {value}")
    return int(dt.timestamp() * 1000)


def close_timestamps(path):
    with open(path, newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or "timestamp" not in reader.fieldnames:
            raise ValueError(f"Missing timestamp column: {path}")
        return [utc_ms(row["timestamp"].replace("Z", "+00:00")) for row in reader]


def audit_file(path, interval, start_ms, end_ms):
    step = INTERVALS[interval]
    actual = close_timestamps(path)
    expected = list(range(start_ms + step - 1, end_ms, step))
    actual_in_window = [ts for ts in actual if start_ms <= ts < end_ms]
    expected_set, actual_set = set(expected), set(actual_in_window)
    missing = sorted(expected_set - actual_set)
    unexpected = sorted(actual_set - expected_set)
    return {
        "file": str(path), "interval": interval,
        "expected_rows": len(expected), "actual_rows_in_window": len(actual_in_window),
        "first_actual_ms": actual[0] if actual else None, "last_actual_ms": actual[-1] if actual else None,
        "missing_expected_rows": len(missing), "unexpected_rows": len(unexpected),
        "missing_examples_ms": missing[:10], "unexpected_examples_ms": unexpected[:10],
        "pass_window_coverage": bool(expected) and not missing and not unexpected and len(actual) == len(actual_in_window),
        "notice": "Exact expected candle coverage only applies to symbols listed throughout the full requested window."
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-dir", default="data/historical")
    p.add_argument("--symbols", nargs="+", default=SYMBOLS)
    p.add_argument("--start", required=True, help="UTC inclusive date YYYY-MM-DD")
    p.add_argument("--end", required=True, help="UTC exclusive date YYYY-MM-DD")
    p.add_argument("--out", default="research-results/window-coverage.json")
    args = p.parse_args()
    start_ms, end_ms = utc_ms(args.start), utc_ms(args.end)
    if end_ms <= start_ms:
        p.error("--end must be later than --start")
    reports = []
    root = Path(args.data_dir)
    for symbol in args.symbols:
        for interval in INTERVALS:
            path = root / f"{symbol}_{interval}.csv"
            try:
                reports.append(audit_file(path, interval, start_ms, end_ms))
            except (OSError, ValueError) as exc:
                reports.append({"file": str(path), "interval": interval,
                                "pass_window_coverage": False, "error": str(exc)})
    result = {"start_utc": args.start, "end_utc_exclusive": args.end,
              "all_window_coverage_pass": bool(reports) and all(r.get("pass_window_coverage", False) for r in reports),
              "reports": reports,
              "notice": "A failed window check means do not compare exit metrics until missing/extra coverage is explained."}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    if not result["all_window_coverage_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
