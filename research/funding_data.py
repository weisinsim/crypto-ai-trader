"""Paginated downloader for Binance USD-M historical funding rates."""
from __future__ import annotations

import argparse
import csv
import time
from math import isfinite
from pathlib import Path
from typing import Callable

import httpx

BASE_URL = "https://fapi.binance.com"
FIELDS = ("funding_time", "funding_rate", "mark_price", "symbol")


def parse_funding_rows(raw_rows: list[dict], symbol: str, start_ms: int, end_ms: int) -> list[dict]:
    rows = []
    for raw in raw_rows:
        ts = int(raw["fundingTime"])
        rate = float(raw["fundingRate"])
        mark_price = float(raw["markPrice"])
        if not isfinite(rate) or not isfinite(mark_price) or mark_price <= 0:
            raise ValueError(f"invalid funding rate or mark price at {ts}")
        if start_ms <= ts < end_ms:
            rows.append({"funding_time": ts, "funding_rate": rate,
                         "mark_price": mark_price, "symbol": symbol.upper()})
    rows.sort(key=lambda row: row["funding_time"])
    for left, right in zip(rows, rows[1:]):
        if left["funding_time"] == right["funding_time"]:
            raise ValueError(f"duplicate funding timestamp: {left['funding_time']}")
    return rows


def download_funding_rates(
    symbol: str, start_ms: int, end_ms: int, *,
    fetch_page: Callable | None = None, limit: int = 1000, pause_seconds: float = 0.05,
) -> list[dict]:
    """Download historical funding records in [start_ms, end_ms), failing on malformed rows."""
    if not isinstance(symbol, str) or not symbol.strip() or not symbol.strip().isalnum():
        raise ValueError("symbol must be a non-empty alphanumeric exchange symbol")
    if start_ms < 0 or end_ms <= start_ms:
        raise ValueError("require 0 <= start_ms < end_ms")
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    if pause_seconds < 0:
        raise ValueError("pause_seconds cannot be negative")

    client = None
    if fetch_page is None:
        client = httpx.Client(timeout=15.0)

        def fetch_page(params):
            response = client.get(BASE_URL + "/fapi/v1/fundingRate", params=params)
            response.raise_for_status()
            return response.json()

    records = []
    cursor = start_ms
    try:
        while cursor < end_ms:
            page = fetch_page({
                "symbol": symbol.upper(), "startTime": cursor,
                "endTime": end_ms - 1, "limit": limit,
            })
            if not page:
                break
            parsed = parse_funding_rows(page, symbol, start_ms, end_ms)
            records.extend(parsed)
            last_ts = max(int(row["fundingTime"]) for row in page)
            next_cursor = last_ts + 1
            if next_cursor <= cursor:
                raise RuntimeError("funding pagination did not advance")
            cursor = next_cursor
            if len(page) < limit:
                break
            if pause_seconds:
                time.sleep(pause_seconds)
    finally:
        if client is not None:
            client.close()

    records.sort(key=lambda row: row["funding_time"])
    by_time = {}
    for record in records:
        if record["funding_time"] in by_time and by_time[record["funding_time"]] != record:
            raise ValueError(f"conflicting funding record at {record['funding_time']}")
        by_time[record["funding_time"]] = record
    return list(by_time.values())


def save_funding_csv(rows: list[dict], path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--start-ms", type=int, required=True)
    parser.add_argument("--end-ms", type=int, required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    rows = download_funding_rates(args.symbol, args.start_ms, args.end_ms)
    save_funding_csv(rows, args.output)
    print(f"status=COMPLETE symbol={args.symbol.upper()} records={len(rows)} output={args.output}")


if __name__ == "__main__":
    main()
