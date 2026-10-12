"""Create an auditable provenance manifest for a funding CSV.

This records provenance claims and structural checks; it does not independently
authenticate the claimed URL or prove that the exchange published every event.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


REQUIRED_COLUMNS = {"funding_time", "funding_rate", "mark_price", "symbol", "mark_price_source"}


def build_manifest(
    csv_path: str | Path,
    *,
    symbol: str,
    start_ms: int,
    end_ms: int,
    source_url: str,
    http_status: int,
    retrieved_at: str,
    expected_interval_hours: float = 8.0,
) -> dict:
    path = Path(csv_path)
    if not path.is_file():
        raise ValueError(f"CSV file does not exist: {path}")
    if start_ms < 0 or end_ms <= start_ms:
        raise ValueError("require 0 <= start_ms < end_ms")
    if not source_url.strip():
        raise ValueError("source_url is required")
    if http_status != 200:
        raise ValueError("source HTTP status must be 200")
    if expected_interval_hours <= 0 or not math.isfinite(expected_interval_hours):
        raise ValueError("expected_interval_hours must be finite and positive")
    try:
        retrieved = datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("retrieved_at must be an ISO-8601 timestamp") from exc
    if retrieved.tzinfo is None:
        raise ValueError("retrieved_at must include a timezone")

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        columns = set(reader.fieldnames or [])
        missing = REQUIRED_COLUMNS - columns
        if missing:
            raise ValueError(f"CSV missing required columns: {sorted(missing)}")
        rows = list(reader)
    if not rows:
        raise ValueError("CSV has no funding rows")

    expected_symbol = symbol.upper()
    events = []
    sources = Counter()
    for row_number, row in enumerate(rows, start=2):
        try:
            ts = int(row["funding_time"])
            rate = float(row["funding_rate"])
            mark = float(row["mark_price"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid numeric value on CSV row {row_number}") from exc
        if not math.isfinite(rate) or not math.isfinite(mark) or mark <= 0:
            raise ValueError(f"invalid rate/mark price on CSV row {row_number}")
        if row["symbol"].strip().upper() != expected_symbol:
            raise ValueError(f"symbol mismatch on CSV row {row_number}")
        if not start_ms <= ts < end_ms:
            raise ValueError(f"funding timestamp outside requested range on CSV row {row_number}: {ts}")
        source = (row.get("mark_price_source") or "").strip() or "unspecified"
        events.append((ts, source))
        sources[source] += 1

    timestamps = [event[0] for event in events]
    if timestamps != sorted(timestamps):
        raise ValueError("funding timestamps are not sorted")
    if len(timestamps) != len(set(timestamps)):
        raise ValueError("duplicate funding timestamps")

    gaps = [(b - a) / 3_600_000 for a, b in zip(timestamps, timestamps[1:])]
    # Report coverage at the requested window boundaries as well as internal gaps.
    # These are diagnostics, not proof of missing events, because funding schedules vary.
    leading_gap_hours = (timestamps[0] - start_ms) / 3_600_000
    trailing_gap_hours = (end_ms - timestamps[-1]) / 3_600_000
    proxy_count = sum(count for name, count in sources.items() if "proxy" in name.lower() or "prior_close" in name.lower())
    unlabelled_count = sources.get("unspecified", 0)
    return {
        "manifest_version": 1,
        "symbol": expected_symbol,
        "requested_range_ms": {"start_inclusive": start_ms, "end_exclusive": end_ms},
        "source_claim": {
            "url": source_url,
            "http_status": http_status,
            "retrieved_at": retrieved.isoformat(),
        },
        "file": {
            "path": str(path),
            "size_bytes": path.stat().st_size,
            "sha256": digest,
        },
        "dataset": {
            "records": len(events),
            "first_funding_time_ms": timestamps[0],
            "last_funding_time_ms": timestamps[-1],
            "mark_price_source_counts": dict(sorted(sources.items())),
            "proxy_or_prior_close_records": proxy_count,
            "unlabelled_source_records": unlabelled_count,
            "first_event_offset_from_start_hours": round(leading_gap_hours, 6),
            "last_event_offset_from_end_hours": round(trailing_gap_hours, 6),
            "max_internal_gap_hours": round(max(gaps, default=0.0), 6),
            "internal_gaps_over_expected_interval": sum(g > expected_interval_hours * 1.01 for g in gaps),
            "boundary_gaps_over_expected_interval": int(leading_gap_hours > expected_interval_hours * 1.01) + int(trailing_gap_hours > expected_interval_hours * 1.01),
            "expected_interval_hours_reference_only": expected_interval_hours,
        },
        "assessment": {
            "status": "REVIEW_REQUIRED" if proxy_count or unlabelled_count else "STRUCTURAL_CHECKS_PASS",
            "source_authenticity_verified": False,
            "event_completeness_verified": False,
            "notes": [
                "Source URL and HTTP status are caller-supplied provenance claims, not independently authenticated.",
                "A file hash proves file identity after manifest creation, not exchange origin.",
                "Gap checks do not prove that every funding event is present.",
                "The expected interval is a diagnostic reference; funding schedules can change.",
                "Exchange mark-price source labels must be verified against the actual acquisition method.",
            ],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, help="Funding CSV using the canonical five-column schema")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--start-ms", type=int, required=True, help="Inclusive UTC epoch milliseconds")
    parser.add_argument("--end-ms", type=int, required=True, help="Exclusive UTC epoch milliseconds")
    parser.add_argument("--source-url", required=True, help="Exact URL used to retrieve the source file")
    parser.add_argument("--http-status", type=int, required=True)
    parser.add_argument("--retrieved-at", required=True, help="ISO-8601 timestamp including timezone")
    parser.add_argument("--expected-interval-hours", type=float, default=8.0)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    manifest = build_manifest(
        args.csv, symbol=args.symbol, start_ms=args.start_ms, end_ms=args.end_ms,
        source_url=args.source_url, http_status=args.http_status,
        retrieved_at=args.retrieved_at,
        expected_interval_hours=args.expected_interval_hours,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, sort_keys=True))
    if manifest["assessment"]["status"] != "STRUCTURAL_CHECKS_PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
