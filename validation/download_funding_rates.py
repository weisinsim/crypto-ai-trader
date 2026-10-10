#!/usr/bin/env python3
"""Download public Binance USD-M Futures funding-rate history.

Output schema is timestamp,funding_rate where timestamp is the funding event
time in UTC and funding_rate is the decimal rate (0.0001 = 0.01%).
"""
import argparse
import csv
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = "https://fapi.binance.com/fapi/v1/fundingRate"
LIMIT = 1000


def utc_ms(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def iso_utc(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def get_json(params, retries=5):
    url = BASE + "?" + urlencode(params)
    for attempt in range(retries):
        try:
            req = Request(url, headers={"User-Agent": "crypto-ai-trader-research/1.0"})
            with urlopen(req, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code not in (418, 429, 500, 502, 503, 504) or attempt == retries - 1:
                raise
            time.sleep(min(2 ** attempt, 30))
        except (URLError, TimeoutError):
            if attempt == retries - 1:
                raise
            time.sleep(min(2 ** attempt, 30))
    raise RuntimeError("Retry budget exhausted")


def download(symbol, start_ms, end_ms):
    rows, cursor = [], start_ms
    while cursor < end_ms:
        batch = get_json({
            "symbol": symbol,
            "startTime": cursor,
            "endTime": end_ms - 1,
            "limit": LIMIT,
        })
        if not batch:
            break
        for item in batch:
            ts = int(item["fundingTime"])
            if start_ms <= ts < end_ms:
                rows.append({"timestamp": iso_utc(ts), "funding_rate": item["fundingRate"]})
        next_cursor = int(batch[-1]["fundingTime"]) + 1
        if next_cursor <= cursor:
            raise RuntimeError(f"Pagination did not advance for {symbol}")
        cursor = next_cursor
        if len(batch) < LIMIT:
            break
        time.sleep(0.15)
    by_ts = {row["timestamp"]: row for row in rows}
    return [by_ts[k] for k in sorted(by_ts)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT", "XRPUSDT"])
    parser.add_argument("--start", required=True, help="UTC start date, e.g. 2024-10-01")
    parser.add_argument("--end", required=True, help="UTC exclusive end date, e.g. 2026-10-01")
    parser.add_argument("--out-dir", default="data/funding")
    args = parser.parse_args()
    start_ms, end_ms = utc_ms(args.start), utc_ms(args.end)
    if start_ms >= end_ms:
        parser.error("--start must be earlier than --end")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for raw_symbol in args.symbols:
        symbol = raw_symbol.upper()
        rows = download(symbol, start_ms, end_ms)
        path = out_dir / f"{symbol}_funding.csv"
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=["timestamp", "funding_rate"])
            writer.writeheader()
            writer.writerows(rows)
        manifest.append({
            "symbol": symbol, "rows": len(rows), "file": str(path),
            "first_timestamp": rows[0]["timestamp"] if rows else None,
            "last_timestamp": rows[-1]["timestamp"] if rows else None,
            "notice": "Verify funding history coverage and event cadence before using it in a backtest."
        })
    print(json.dumps({"notice": "Public funding data downloaded; no profitability claim.", "files": manifest}, indent=2))


if __name__ == "__main__":
    main()
