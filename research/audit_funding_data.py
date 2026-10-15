"""Audit historical funding CSV against candle timestamps without running a backtest."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from research.run_backtest import load_csv, load_funding_csv


def audit_funding_against_candles(\n    candle_rows: list[dict], funding_rows: list[dict], symbol: str,\n    max_gap_hours: float | None = None,\n) -> dict:
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

    if max_gap_hours is not None and (not isinstance(max_gap_hours, (int, float)) or max_gap_hours <= 0):\n        raise ValueError("max_gap_hours must be positive when provided")\n\n    candle_ts = {int(row["t"]) for row in candle_rows}
    start_ts, end_ts = min(candle_ts), max(candle_ts)
    in_range = [row for row in funding_rows if start_ts <= int(row["funding_time"]) <= end_ts]
    out_of_range = len(funding_rows) - len(in_range)
    matched = sum(int(row["funding_time"]) in candle_ts for row in in_range)
    deltas = [
        int(right["funding_time"]) - int(left["funding_time"])
        for left, right in zip(funding_rows, funding_rows[1:])
    ]
    interval_counts = Counter(deltas)
    return {
        "status": "REVIEW_REQUIRED" if out_of_range or matched != len(in_range) else "TIMESTAMP_ALIGNMENT_PASS_NOT_RECONCILED",
        "symbol": symbol,
        "candle_range": {"start_ts": start_ts, "end_ts": end_ts, "count": len(candle_rows)},
        "funding": {
            "total_records": len(funding_rows),
            "records_in_candle_range": len(in_range),
            "records_outside_candle_range": out_of_range,
            "exact_candle_open_matches": matched,
            "nonmatching_in_range": len(in_range) - matched,
            "first_event_ts": int(funding_rows[0]["funding_time"]),
            "last_event_ts": int(funding_rows[-1]["funding_time"]),
            "source_labels": sorted({row.get("mark_price_source", "unspecified") for row in funding_rows}),
            "venue_labels": sorted({row.get("venue", "") or "unspecified" for row in funding_rows}),
            "event_interval_ms": {
                "min": min(deltas) if deltas else None,
                "max": max(deltas) if deltas else None,
                "distinct_count": len(interval_counts),
                "counts": {str(k): v for k, v in sorted(interval_counts.items())},\n                "intervals_over_threshold_ms": large_gaps,
            },
        },
        "event_completeness_verified": False,
        "source_authenticity_verified": False,
        "account_cashflows_reconciled": False,
        "note": (
            "Timestamp alignment is not proof of complete event coverage, authenticated mark prices, "
            "settlement eligibility, or agreement with account cashflows. Nonmatching events are reported, "
            "never rounded or snapped to candle timestamps."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candles", required=True, help="OHLCV CSV with t,o,h,l,c columns")
    parser.add_argument("--funding-csv", required=True, help="Historical funding CSV")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--interval", default="1h", choices=("1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"))
    parser.add_argument("--output", required=True)\n    parser.add_argument("--max-gap-hours", type=float, default=None,\n                        help="Optional review threshold for unusually long funding-event gaps; does not prove missing events")
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
