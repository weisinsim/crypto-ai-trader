"""Audit historical funding CSV against candle timestamps without running a backtest."""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path

from research.run_backtest import load_csv, load_funding_csv


def audit_funding_against_candles(
    candle_rows: list[dict], funding_rows: list[dict], symbol: str,
    max_gap_hours: float | None = None,
) -> dict:
    if not candle_rows:
        raise ValueError("candle data is empty")
    symbol = symbol.strip().upper()
    if not symbol:
        raise ValueError("symbol must be non-empty")
    if not funding_rows:
        raise ValueError("funding data is empty")
    symbols = {row.get("symbol", "").strip().upper() for row in funding_rows}
    if symbols != {symbol}:
        raise ValueError(f"funding symbols {sorted(symbols)} do not match requested symbol {symbol}")

    if max_gap_hours is not None and (
        isinstance(max_gap_hours, bool)
        or not isinstance(max_gap_hours, (int, float))
        or not math.isfinite(max_gap_hours)
        or max_gap_hours <= 0
    ):
        raise ValueError("max_gap_hours must be a finite positive number when provided")

    candle_ts = {int(row["t"]) for row in candle_rows}
    start_ts, end_ts = min(candle_ts), max(candle_ts)
    # Audit the exchange-reported timestamp when available. The backtest loader
    # may separately normalize tiny boundary jitter for settlement calculations.
    def audit_ts(row: dict) -> int:
        return int(row.get("funding_time_raw", row["funding_time"]))

    event_timestamps = sorted(audit_ts(row) for row in funding_rows)
    timestamp_counts = Counter(event_timestamps)
    duplicate_timestamps = sorted(ts for ts, count in timestamp_counts.items() if count > 1)
    duplicate_event_records = sum(count - 1 for count in timestamp_counts.values() if count > 1)
    in_range = [ts for ts in event_timestamps if start_ts <= ts <= end_ts]
    out_of_range = len(event_timestamps) - len(in_range)
    matched = sum(ts in candle_ts for ts in in_range)
    deltas = [right - left for left, right in zip(event_timestamps, event_timestamps[1:])]
    interval_counts = Counter(deltas)
    max_gap_ms = max_gap_hours * 3_600_000 if max_gap_hours is not None else None
    large_gaps = [delta for delta in deltas if max_gap_ms is not None and delta > max_gap_ms]
    needs_review = bool(
        out_of_range or matched != len(in_range) or large_gaps or duplicate_event_records
    )
    return {
        "status": "REVIEW_REQUIRED" if needs_review else "TIMESTAMP_ALIGNMENT_PASS_NOT_RECONCILED",
        "gap_threshold_hours": max_gap_hours,
        "intervals_exceeding_gap_threshold": len(large_gaps),
        "duplicate_event_records": duplicate_event_records,
        "duplicate_event_timestamps": duplicate_timestamps,
        "symbol": symbol,
        "candle_range": {"start_ts": start_ts, "end_ts": end_ts, "count": len(candle_rows)},
        "funding": {
            "total_records": len(funding_rows),
            "records_in_candle_range": len(in_range),
            "records_outside_candle_range": out_of_range,
            "exact_candle_open_matches": matched,
            "nonmatching_in_range": len(in_range) - matched,
            "first_event_ts": event_timestamps[0],
            "last_event_ts": event_timestamps[-1],
            "source_labels": sorted({row.get("mark_price_source", "unspecified") for row in funding_rows}),
            "venue_labels": sorted({row.get("venue", "") or "unspecified" for row in funding_rows}),
            "event_interval_ms": {
                "min": min(deltas) if deltas else None,
                "max": max(deltas) if deltas else None,
                "distinct_count": len(interval_counts),
                "counts": {str(k): v for k, v in sorted(interval_counts.items())},
                "intervals_over_threshold_ms": large_gaps,
            },
        },
        "event_completeness_verified": False,
        "source_authenticity_verified": False,
        "account_cashflows_reconciled": False,
        "note": (
            "Timestamp alignment uses the raw exchange-reported event time when available and is not proof "
            "of complete event coverage, authenticated mark prices, settlement eligibility, or agreement "
            "with account cashflows. Nonmatching events are reported without snapping in this audit."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candles", required=True, help="OHLCV CSV with t,o,h,l,c columns")
    parser.add_argument("--funding-csv", required=True, help="Historical funding CSV")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--interval", default="1h", choices=("1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"))
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-gap-hours", type=float, default=None,
                        help="Optional review threshold for unusually long funding-event gaps; does not prove missing events")
    args = parser.parse_args(argv)
    interval_ms = {
        "1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
        "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "1d": 86_400_000,
    }[args.interval]
    candles = load_csv(args.candles, interval_ms)
    funding = load_funding_csv(args.funding_csv)
    report = audit_funding_against_candles(candles, funding, args.symbol, args.max_gap_hours)
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "output": str(path), "symbol": report["symbol"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
