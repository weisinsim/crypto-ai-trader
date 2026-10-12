"""Audit completeness and provenance of historical exchange funding CSVs."""
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
                  max_gap_hours: float = 24.0, expected_venue: str | None = None) -> dict:
    if not funding_rows or not candle_rows:
        raise ValueError("funding and candle datasets must both be non-empty")
    if not math.isfinite(max_gap_hours) or max_gap_hours <= 0:
        raise ValueError("max_gap_hours must be finite and greater than zero")
    symbol = symbol.upper()
    if expected_venue is not None:
        expected_venue = expected_venue.strip().lower()
        if not expected_venue:
            raise ValueError("expected_venue must be non-empty when provided")
    symbols = {str(row.get("symbol", "")).upper() for row in funding_rows}
    if symbols != {symbol}:
        raise ValueError(f"symbol mismatch: expected {symbol}, got {sorted(symbols)}")
    events = []
    for row in funding_rows:
        ts, rate, mark = int(row["funding_time"]), float(row["funding_rate"]), float(row["mark_price"])
        if not math.isfinite(rate) or not math.isfinite(mark) or mark <= 0:
            raise ValueError(f"invalid funding record at {ts}")
        events.append((ts, str(row.get("mark_price_source", "unspecified") or "unspecified"),
                       str(row.get("venue", "") or "").strip().lower()))
    timestamps = [ts for ts, _, _ in events]
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
    leading_gap = (timestamps[0] - start) / 3_600_000
    trailing_gap = (end - timestamps[-1]) / 3_600_000
    gaps = [(b-a)/3_600_000 for a, b in zip(timestamps, timestamps[1:])]
    max_internal_gap = max(gaps, default=0.0)
    max_gap = max([leading_gap, trailing_gap, *gaps], default=0.0)
    sources = Counter(source for _, source, _ in events)
    approximate_markers = ("proxy", "prior_close", "nearest_1s")
    proxy_count = sum(n for name, n in sources.items()
                      if any(marker in name.lower() for marker in approximate_markers))
    # Recognize exact-time official mark-price candle opens for the venue used
    # by the confirmation. Labels identify the acquisition path, not authenticity.
    approved_sources = {
        "mark_kline_open",
        "binance_fapi_funding_rate_mark_price",
        "okx_mark_price_kline_open",
    }
    unverified_events = [
        (ts, source, venue) for ts, source, venue in events
        if source not in approved_sources
        or (source == "okx_mark_price_kline_open" and venue != "okx")
        or (source == "binance_fapi_funding_rate_mark_price" and venue != "binance")
        or (expected_venue is not None and venue != expected_venue)
    ]
    unverified_count = len(unverified_events)
    unverified_records = [
        {"funding_time": ts, "mark_price_source": source, **({"venue": venue} if venue else {})}
        for ts, source, venue in unverified_events
    ]
    expected = (end-start)/(8*3_600_000)
    if max_gap > max_gap_hours and proxy_count:
        status = "FAIL_GAP_AND_PROXY_PRICE"
    elif max_gap > max_gap_hours and unverified_count:
        status = "FAIL_GAP_AND_UNVERIFIED_SOURCE"
    elif max_gap > max_gap_hours:
        status = "FAIL_GAP"
    elif proxy_count:
        status = "FAIL_PROXY_MARK_PRICE"
    elif unverified_count:
        status = "FAIL_UNVERIFIED_MARK_PRICE_SOURCE"
    else:
        status = "PASS"
    return {
        "status": status, "symbol": symbol, "candle_bars": len(candle_rows),
        "candle_first_ts": start, "candle_last_open_ts": candle_times[-1],
        "funding_records": len(events), "funding_first_ts": timestamps[0],
        "funding_last_ts": timestamps[-1], "expected_events_if_8h_schedule": round(expected),
        "count_ratio_vs_8h_schedule": round(len(events)/expected, 4) if expected else None,
        "max_gap_hours": round(max_gap, 4), "max_internal_gap_hours": round(max_internal_gap, 4),
        "leading_gap_hours": round(leading_gap, 4), "trailing_gap_hours": round(trailing_gap, 4),
        "gaps_over_8h": sum(g > 8.01 for g in gaps),
        "gaps_over_24h": sum(g > 24.01 for g in gaps),
        "boundary_gaps_over_8h": int(leading_gap > 8.01) + int(trailing_gap > 8.01),
        "mark_price_source_counts": dict(sorted(sources.items())),
        "proxy_price_records": proxy_count,
        "unverified_price_source_records": unverified_count,
        "unverified_records": unverified_records,
        "approved_mark_price_sources": sorted(approved_sources),
        "max_gap_gate_hours": max_gap_hours,
        "expected_venue": expected_venue,
        "limitations": [
            "Gap checks do not prove that every funding event is present.",
            "Leading and trailing coverage are checked against candle-window boundaries.",
            "The 8-hour schedule is a reference, not an assumption of complete coverage.",
            "Proxy and nearest-timestamp prices are not accepted as exact settlement observations.",
            "Recognized labels do not independently authenticate source files.",
            "OKX mark prices are only appropriate for a matching OKX instrument and venue."
        ]
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--funding-csv", required=True)
    parser.add_argument("--candle-csv", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-gap-hours", type=float, default=24.0)
    parser.add_argument("--expected-venue", help="require all funding rows to match this venue")
    args = parser.parse_args()
    report = audit_funding(read_rows(args.funding_csv), read_rows(args.candle_csv),
                           args.symbol, args.max_gap_hours, args.expected_venue)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    if report["status"] != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
