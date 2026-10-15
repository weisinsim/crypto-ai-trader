"""Paginated Binance USD-M historical funding-rate downloader.

The endpoint's markPrice field is retained as reported by Binance. This tool
records source metadata but does not independently authenticate coverage or
reconcile funding cashflows against an account ledger.
"""
from __future__ import annotations

import argparse
import csv
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import httpx

BASE_URL = "https://fapi.binance.com"
FIELDS = (
    "funding_time", "funding_rate", "mark_price", "symbol",
    "mark_price_source", "venue",
)


def normalize_funding_rows(raw_rows: list[dict], symbol: str) -> list[dict]:
    rows = []
    for raw in raw_rows:
        funding_time = int(raw["fundingTime"])
        rate = float(raw["fundingRate"])
        mark_price = float(raw["markPrice"])
        if funding_time < 0:
            raise ValueError("funding timestamp cannot be negative")
        if not rate == rate or rate in (float("inf"), float("-inf")):
            raise ValueError("funding rate must be finite")
        if not mark_price > 0 or mark_price != mark_price or mark_price in (float("inf"), float("-inf")):
            raise ValueError("mark price must be finite and positive")
        rows.append({
            "funding_time": funding_time,
            "funding_rate": rate,
            "mark_price": mark_price,
            "symbol": symbol.upper(),
            "mark_price_source": "binance_funding_rate_endpoint_markPrice",
            "venue": "binance_usdm",
        })
    rows.sort(key=lambda row: row["funding_time"])
    for left, right in zip(rows, rows[1:]):
        if left["funding_time"] == right["funding_time"]:
            raise ValueError(f"duplicate funding timestamp: {left['funding_time']}")
    return rows


def download_funding_history(
    symbol: str,
    start_ms: int,
    end_ms: int,
    *,
    fetch_page: Callable | None = None,
    limit: int = 1000,
    pause_seconds: float = 0.05,
) -> list[dict]:
    """Download funding events in [start_ms, end_ms), using event-time pagination."""
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

    raw_rows = []
    cursor = start_ms
    try:
        while cursor < end_ms:
            page = fetch_page({
                "symbol": symbol.upper(),
                "startTime": cursor,
                "endTime": end_ms - 1,
                "limit": limit,
            })
            if not page:
                break
            ordered = sorted(page, key=lambda row: int(row["fundingTime"]))
            in_range = [
                row for row in ordered
                if start_ms <= int(row["fundingTime"]) < end_ms
            ]
            raw_rows.extend(in_range)
            last_ts = max(int(row["fundingTime"]) for row in page)
            if last_ts < cursor:
                raise RuntimeError("funding pagination moved backwards")
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

    rows = normalize_funding_rows(raw_rows, symbol)
    if not rows:
        raise ValueError("no funding events returned in requested range")
    if rows[0]["funding_time"] < start_ms or rows[-1]["funding_time"] >= end_ms:
        raise AssertionError("funding rows escaped requested range")
    return rows


def save_csv(rows: list[dict], path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def utc_ms(date_text: str) -> int:
    dt = datetime.strptime(date_text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True, help="e.g. BTCUSDT")
    parser.add_argument("--start", required=True, help="UTC date YYYY-MM-DD, inclusive")
    parser.add_argument("--end", required=True, help="UTC date YYYY-MM-DD, exclusive")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    rows = download_funding_history(args.symbol, utc_ms(args.start), utc_ms(args.end))
    save_csv(rows, args.output)
    print(
        f"status=DOWNLOADED_NOT_RECONCILED venue=binance_usdm "
        f"symbol={args.symbol.upper()} events={len(rows)} "
        f"first_ts={rows[0]['funding_time']} last_ts={rows[-1]['funding_time']} "
        f"output={args.output}"
    )


if __name__ == "__main__":
    main()
