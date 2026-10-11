"""Audit completeness and provenance of historical Binance funding CSVs."""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from pathlib import Path


def read_rows(path: str | Path) -> list[dict]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"empty CSV: {path}")
    return rows


def audit_funding(funding_rows: list[dict], candle_rows: list[dict], symbol: str,
                  max_gap_hours: float = 24.0) -> dict:
    if not funding_rows or not candle_rows:
        raise ValueError("funding and candle datasets must both be non-empty")
    symbol = symbol.upper()
    symbols = {str(row.get("symbol", "")).upper() for row in funding_rows}
    if symbols != {symbol}:
        raise ValueError(f"symbol mismatch: expected {symbol}, got {sorted(symbols)}")
    events = []
    for row in funding_rows:
        ts, rate, mark = int(row["funding_time"]), float(row["funding_rate"]), float(row["mark_price"])
        if not math.isfinite(rate) or not math.isfinite(mark) or mark <= 0:
            raise ValueError(f"invalid funding record at {ts}")
        events.append((ts, str(row.get("mark_price_source", "unspecified") or "unspecified")))
    timestamps = [ts for ts, _ in events]
    if timestamps != sorted(timestamps):
        raise ValueError("funding timestamps are not sorted")
    if len(set(timestamps)) != len(timestamps):
        raise ValueError("duplicate funding timestamps")
    candle_times = [int(row["t"]) for row in candle_rows]
    if candle_times != sorted(candle_times) or len(set(candle_times)) != len(candle_times):
        raise ValueError("candle timestamps are not sorted and unique")
    start, end = candle_times[0], candle_times[-1] + 3_600_000
    if any(ts < start or ts >= end for ts in timestamps):
        raise ValueError("funding records extend outside candle coverage")
    gaps = [(b-a)/3_600_000 for a, b in zip(timestamps, timestamps[1:])]
    max_gap = max(gaps, default=0.0)
    sources = Counter(source for _, source in events)
    proxy_count = sum(n for name, n in sources.items() if "proxy" in name.lower())
    expected = (end-start)/(8*3_600_000)
    # A proxy close is not an exchange mark-price observation. Do not label a
    # dataset clean when any funding cashflow uses a proxy valuation.
    if max_gap > max_gap_hours and proxy_count:
        status = "FAIL_GAP_AND_PROXY_PRICE"
    elif max_gap > max_gap_hours:
        status = "FAIL_GAP"
    elif proxy_count:
        status = "FAIL_PROXY_MARK_PRICE"
    else:
        status = "PASS"
    return {
        "status": status,
        "symbol": symbol, "candle_bars": len(candle_rows),
        "candle_first_ts": start, "candle_last_open_ts": candle_times[-1],
        "funding_records": len(events), "funding_first_ts": timestamps[0],
        "funding_last_ts": timestamps[-1], "expected_events_if_8h_schedule": round(expected),
        "count_ratio_vs_8h_schedule": round(len(events)/expected, 4) if expected else None,
        "max_gap_hours": round(max_gap, 4),
        "gaps_over_8h": sum(g > 8.01 for g in gaps),
        "gaps_over_24h": sum(g > 24.01 for g in gaps),
        "mark_price_source_counts": dict(sorted(sources.items())),
        "proxy_price_records": proxy_count,
        "max_gap_gate_hours": max_gap_hours,
        "limitations": [
            "Gap checks do not prove that every funding event is present.",
            "The 8-hour schedule is a reference, not an assumption of complete coverage.",
            "Proxy prices are not exchange mark-price observations."
        ]
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--funding-csv", required=True)
    parser.add_argument("--candle-csv", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-gap-hours", type=float, default=24.0)
    args = parser.parse_args()
    report = audit_funding(read_rows(args.funding_csv), read_rows(args.candle_csv),
                           args.symbol, args.max_gap_hours)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    if report["status"] != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
