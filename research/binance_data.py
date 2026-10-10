"""Paginated Binance USD-M futures OHLCV downloader with explicit coverage checks."""
from __future__ import annotations

import argparse
import csv
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import httpx

BASE_URL = "https://fapi.binance.com"
INTERVAL_MS = {"1h": 3_600_000, "4h": 14_400_000}
FIELDS = ("t", "o", "h", "l", "c", "v", "q")


def parse_klines(raw_rows: list[list], interval_ms: int, end_ms: int) -> list[dict]:
    """Normalize exchange kline arrays and exclude candles not closed by end_ms."""
    rows = []
    for raw in raw_rows:
        if len(raw) < 8:
            raise ValueError("kline row has fewer than 8 fields")
        ts = int(raw[0])
        if ts + interval_ms > end_ms:
            continue
        rows.append({
            "t": ts, "o": float(raw[1]), "h": float(raw[2]),
            "l": float(raw[3]), "c": float(raw[4]), "v": float(raw[5]),
            "q": float(raw[7]),
        })
    rows.sort(key=lambda r: r["t"])
    seen = set()
    for row in rows:
        if row["t"] in seen:
            raise ValueError(f"duplicate kline timestamp: {row['t']}")
        seen.add(row["t"])
    return rows


def download_klines(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    *,
    fetch_page: Callable | None = None,
    limit: int = 1500,
    pause_seconds: float = 0.05,
) -> list[dict]:
    """Fetch [start_ms, end_ms) candle coverage using forward pagination.

    Inject fetch_page(params) for deterministic tests. Raises rather than
    silently claiming complete coverage when a timestamp gap is detected.
    """
    if not isinstance(symbol, str) or not symbol.strip() or not symbol.strip().isalnum():
        raise ValueError("symbol must be a non-empty alphanumeric exchange symbol")
    if interval not in INTERVAL_MS:
        raise ValueError(f"unsupported interval: {interval}")
    if pause_seconds < 0:
        raise ValueError("pause_seconds cannot be negative")
    if start_ms < 0 or end_ms <= start_ms:
        raise ValueError("require 0 <= start_ms < end_ms")
    if not 1 <= limit <= 1500:
        raise ValueError("limit must be between 1 and 1500")
    duration = INTERVAL_MS[interval]
    if start_ms % duration or end_ms % duration:
        raise ValueError("start_ms and end_ms must align to interval boundaries")

    client = None
    if fetch_page is None:
        client = httpx.Client(timeout=15.0)
        def fetch_page(params):
            response = client.get(BASE_URL + "/fapi/v1/klines", params=params)
            response.raise_for_status()
            return response.json()

    rows = []
    cursor = start_ms
    try:
        while cursor < end_ms:
            page = fetch_page({
                "symbol": symbol.upper(), "interval": interval,
                "startTime": cursor, "endTime": end_ms - 1, "limit": limit,
            })
            if not page:
                break
            parsed = parse_klines(page, duration, end_ms)
            rows.extend(r for r in parsed if start_ms <= r["t"] < end_ms)
            last_ts = max(int(r[0]) for r in page)
            next_cursor = last_ts + duration
            if next_cursor <= cursor:
                raise RuntimeError("pagination did not advance; aborting to avoid an infinite loop")
            cursor = next_cursor
            if len(page) < limit:
                break
            if pause_seconds:
                time.sleep(pause_seconds)
    finally:
        if client is not None:
            client.close()

    rows.sort(key=lambda r: r["t"])
    unique = {}
    for row in rows:
        unique[row["t"]] = row
    rows = list(unique.values())
    expected = list(range(start_ms, end_ms, duration))
    actual = [r["t"] for r in rows]
    if actual != expected:
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        raise ValueError(
            f"incomplete coverage for {symbol} {interval}: expected {len(expected)} bars, "
            f"got {len(actual)}; missing_first={missing[:5]}, extra_first={extra[:5]}"
        )
    return rows


def save_csv(rows: list[dict], path: str | Path) -> None:
    """Write stable, timestamp-sorted OHLCV CSV."""
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
    parser.add_argument("--interval", choices=sorted(INTERVAL_MS), required=True)
    parser.add_argument("--start", required=True, help="UTC date YYYY-MM-DD, inclusive")
    parser.add_argument("--end", required=True, help="UTC date YYYY-MM-DD, exclusive")
    parser.add_argument("--output", required=True, help="CSV output path")
    args = parser.parse_args()
    start_ms, end_ms = utc_ms(args.start), utc_ms(args.end)
    rows = download_klines(args.symbol, args.interval, start_ms, end_ms)
    save_csv(rows, args.output)
    print(f"status=COMPLETE symbol={args.symbol.upper()} interval={args.interval} "
          f"bars={len(rows)} start_ms={rows[0]['t']} end_ms={rows[-1]['t']} "
          f"output={args.output}")


if __name__ == "__main__":
    main()
