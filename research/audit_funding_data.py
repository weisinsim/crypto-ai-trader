"""Audit historical funding CSV against candle timestamps without running a backtest."""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path

from research.run_backtest import load_csv, load_funding_csv, source_file_manifest


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

    def timestamp_ms(value, field: str) -> int:
        # Reject timestamps that would be silently truncated or treated as booleans.
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ValueError(f"{field} must be a positive integer timestamp in milliseconds")
        try:
            parsed = int(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{field} must be a positive integer timestamp in milliseconds") from exc
        if parsed <= 0 or (isinstance(value, str) and value.strip() != str(parsed)):
            raise ValueError(f"{field} must be a positive integer timestamp in milliseconds")
        return parsed

    def normalized_symbol(row: dict) -> str:
        value = row.get("symbol", "")
        if not isinstance(value, str):
            return ""
        return value.strip().upper()

    symbols = {normalized_symbol(row) for row in funding_rows}
    if symbols != {symbol}:
        raise ValueError(f"funding symbols {sorted(symbols)} do not match requested symbol {symbol}")

    if max_gap_hours is not None and (
        isinstance(max_gap_hours, bool)
        or not isinstance(max_gap_hours, (int, float))
        or not math.isfinite(max_gap_hours)
        or max_gap_hours <= 0
    ):
        raise ValueError("max_gap_hours must be a finite positive number when provided")

    candle_timestamps = [timestamp_ms(row.get("t"), "candle timestamp") for row in candle_rows]
    candle_order_valid = all(
        right > left for left, right in zip(candle_timestamps, candle_timestamps[1:])
    )
    candle_timestamp_counts = Counter(candle_timestamps)
    duplicate_candle_timestamps = sorted(
        ts for ts, count in candle_timestamp_counts.items() if count > 1
    )
    candle_ts = set(candle_timestamps)
    start_ts, end_ts = min(candle_ts), max(candle_ts)
    # Audit the exchange-reported timestamp when available. The backtest loader
    # may separately normalize tiny boundary jitter for settlement calculations.
    def audit_ts(row: dict) -> int:
        return timestamp_ms(row.get("funding_time_raw", row.get("funding_time")), "funding timestamp")

    event_timestamps = sorted(audit_ts(row) for row in funding_rows)
    timestamp_counts = Counter(event_timestamps)
    duplicate_timestamps = sorted(ts for ts, count in timestamp_counts.items() if count > 1)
    duplicate_event_records = sum(count - 1 for count in timestamp_counts.values() if count > 1)

    # Raw exchange timestamps are retained for audit and cadence diagnostics.
    # A separate alignment timestamp may use the loader's canonicalized value
    # only when the raw value is within 1 second and the canonical timestamp is
    # an actual candle open. This reports documented boundary jitter without
    # silently rounding arbitrary event times onto candles.
    timestamp_pairs = []
    jitter_normalized = 0
    for row in funding_rows:
        raw_ts = audit_ts(row)
        normalized_ts = int(row.get("funding_time", raw_ts))
        if (normalized_ts != raw_ts and abs(normalized_ts - raw_ts) <= 1000
                and normalized_ts in candle_ts):
            alignment_ts = normalized_ts
            jitter_normalized += 1
        else:
            alignment_ts = raw_ts
        timestamp_pairs.append((raw_ts, alignment_ts))
    timestamp_pairs.sort(key=lambda pair: pair[0])
    in_range_pairs = [
        (raw_ts, align_ts) for raw_ts, align_ts in timestamp_pairs
        if start_ts <= raw_ts <= end_ts
    ]
    out_of_range = len(event_timestamps) - len(in_range_pairs)
    raw_matched = sum(raw_ts in candle_ts for raw_ts, _ in in_range_pairs)
    matched = sum(align_ts in candle_ts for _, align_ts in in_range_pairs)
    nonmatching = len(in_range_pairs) - matched
    deltas = [right - left for left, right in zip(event_timestamps, event_timestamps[1:])]
    interval_counts = Counter(deltas)
    max_gap_ms = max_gap_hours * 3_600_000 if max_gap_hours is not None else None
    large_gaps = [delta for delta in deltas if max_gap_ms is not None and delta > max_gap_ms]
    in_range_event_ts = [raw_ts for raw_ts, _ in in_range_pairs]
    leading_gap_ms = in_range_event_ts[0] - start_ts if in_range_event_ts else None
    trailing_gap_ms = end_ts - in_range_event_ts[-1] if in_range_event_ts else None
    edge_gaps_exceeding_threshold = (
        max_gap_ms is not None and (
            leading_gap_ms is None or trailing_gap_ms is None
            or leading_gap_ms > max_gap_ms or trailing_gap_ms > max_gap_ms
        )
    )
    def label_values(field: str, default: str) -> list[str]:
        # CSV-loaded rows normally contain strings, but this public audit helper
        # also accepts direct callers. Keep malformed/null labels serializable and
        # deterministic instead of letting mixed types crash sorted().
        values = set()
        for row in funding_rows:
            value = row.get(field)
            if value is None or value == "":
                values.add(default)
            elif isinstance(value, str):
                values.add(value)
            else:
                values.add(f"invalid:{type(value).__name__}")
        return sorted(values)

    def provenance_label_issues(field: str) -> int:
        # Provenance is usable only when every event has a non-empty text label.
        # A display fallback such as "unspecified" must never count as provenance.
        return sum(
            1 for row in funding_rows
            if not isinstance(row.get(field), str) or not row.get(field, "").strip()
        )

    missing_source_labels = provenance_label_issues("mark_price_source")
    missing_venue_labels = provenance_label_issues("venue")
    needs_review = bool(
        not candle_order_valid or duplicate_candle_timestamps or out_of_range or nonmatching or large_gaps
        or duplicate_event_records or edge_gaps_exceeding_threshold
        or missing_source_labels or missing_venue_labels
    )

    return {
        "status": "REVIEW_REQUIRED" if needs_review else (
            "BOUNDARY_JITTER_NORMALIZED_ALIGNMENT_PASS_NOT_RECONCILED"
            if jitter_normalized else "TIMESTAMP_ALIGNMENT_PASS_NOT_RECONCILED"
        ),
        "gap_threshold_hours": max_gap_hours,
        "intervals_exceeding_gap_threshold": len(large_gaps),
        "candle_timestamps_strictly_increasing": candle_order_valid,
        "duplicate_candle_timestamps": duplicate_candle_timestamps,
        "duplicate_candle_records": sum(
            count - 1 for count in candle_timestamp_counts.values() if count > 1
        ),
        "duplicate_event_records": duplicate_event_records,
        "duplicate_event_timestamps": duplicate_timestamps,
        "symbol": symbol,
        "candle_range": {"start_ts": start_ts, "end_ts": end_ts, "count": len(candle_rows)},
        "funding": {
            "total_records": len(funding_rows),
            "records_in_candle_range": len(in_range_pairs),
            "records_outside_candle_range": out_of_range,
            "exact_candle_open_matches": raw_matched,
            "normalized_candle_open_matches": matched,
            "boundary_jitter_normalized_records": jitter_normalized,
            "nonmatching_in_range": nonmatching,
            "first_event_ts": event_timestamps[0],
            "last_event_ts": event_timestamps[-1],
            "source_labels": label_values("mark_price_source", "unspecified"),
            "venue_labels": label_values("venue", "unspecified"),
            "records_missing_or_invalid_source_label": missing_source_labels,
            "records_missing_or_invalid_venue_label": missing_venue_labels,
            "event_interval_ms": {
                "min": min(deltas) if deltas else None,
                "max": max(deltas) if deltas else None,
                "distinct_count": len(interval_counts),
                "counts": {str(k): v for k, v in sorted(interval_counts.items())},
                "intervals_over_threshold_ms": large_gaps,
                "range_edge_gaps_ms": {
                    "leading_from_first_candle_to_first_funding_event": leading_gap_ms,
                    "trailing_from_last_funding_event_to_last_candle": trailing_gap_ms,
                    "edge_gaps_exceeding_threshold": edge_gaps_exceeding_threshold,
                },
            },
        },
        "event_completeness_verified": False,
        "source_authenticity_verified": False,
        "account_cashflows_reconciled": False,
        "note": (
            "Raw exchange timestamps and event cadence are preserved. For alignment only, a timestamp may use "
            "the loader's normalized value when it differs by at most 1 second and that normalized value is "
            "an actual candle open. This handles known boundary jitter without snapping arbitrary times. "
            "Alignment is not proof of complete event coverage, authenticated mark prices, settlement eligibility, "
            "or agreement with account cashflows."
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
    # Hash the exact input bytes for reproducibility; a digest is not source authentication.
    report["input_manifest"] = {
        "candles": source_file_manifest(args.candles),
        "funding": source_file_manifest(args.funding_csv),
        "hash_algorithm": "sha256",
        "source_authenticity_verified": False,
    }
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "output": str(path), "symbol": report["symbol"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
