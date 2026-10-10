#!/usr/bin/env python3
"""Audit timestamped funding CSVs without assuming a fixed funding cadence.

Input schema: timestamp,funding_rate. Funding cadence can vary by symbol and
historical period, so this tool reports event gaps rather than declaring every
non-8-hour interval invalid.
"""
import argparse
import csv
import json
import math
from datetime import datetime
from pathlib import Path


def parse_timestamp(value):
    dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(f"Funding timestamp must include timezone: {value!r}")
    return int(dt.timestamp() * 1000)


def audit(path, max_gap_hours=24.0):
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as stream:
        for line_number, row in enumerate(csv.DictReader(stream), start=2):
            try:
                ts = parse_timestamp(row["timestamp"])
                rate = float(row["funding_rate"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"Invalid funding row at line {line_number}: {exc}") from exc
            if not math.isfinite(rate):
                raise ValueError(f"Non-finite funding rate at line {line_number}")
            rows.append({"ts": ts, "rate": rate})
    if not rows:
        return {"file": str(path), "rows": 0, "pass_basic_integrity": False,
                "error": "EMPTY_FUNDING_HISTORY"}
    timestamps = [row["ts"] for row in rows]
    duplicates = len(timestamps) - len(set(timestamps))
    non_increasing = sum(b <= a for a, b in zip(timestamps, timestamps[1:]))
    gaps = [(b - a) / 1000 for a, b in zip(timestamps, timestamps[1:]) if b > a]
    threshold_seconds = max_gap_hours * 3600
    large_gaps = [gap for gap in gaps if gap > threshold_seconds]
    ordered = timestamps == sorted(timestamps)
    passed = duplicates == 0 and non_increasing == 0 and ordered and not large_gaps
    return {
        "file": str(path), "rows": len(rows),
        "first_timestamp_ms": timestamps[0], "last_timestamp_ms": timestamps[-1],
        "duplicates": duplicates, "non_increasing_pairs": non_increasing,
        "ordered": ordered, "max_gap_hours": round(max(gaps) / 3600, 4) if gaps else None,
        "gaps_over_threshold": len(large_gaps),
        "largest_gap_examples_hours": sorted((round(g / 3600, 4) for g in large_gaps), reverse=True)[:10],
        "configured_max_gap_hours": max_gap_hours,
        "pass_basic_integrity": passed,
        "notice": "Gap threshold is a coarse diagnostic; verify symbol-specific historical funding cadence and requested window.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", help="Funding CSV files")
    parser.add_argument("--max-gap-hours", type=float, default=24.0)
    args = parser.parse_args()
    if not math.isfinite(args.max_gap_hours) or args.max_gap_hours <= 0:
        parser.error("--max-gap-hours must be finite and > 0")
    reports = []
    for filename in args.files:
        path = Path(filename)
        try:
            reports.append(audit(path, args.max_gap_hours))
        except (OSError, ValueError) as exc:
            reports.append({"file": str(path), "pass_basic_integrity": False, "error": str(exc)})
    print(json.dumps({"all_basic_integrity_pass": bool(reports) and all(x.get("pass_basic_integrity", False) for x in reports),
                      "reports": reports}, indent=2))


if __name__ == "__main__":
    main()
