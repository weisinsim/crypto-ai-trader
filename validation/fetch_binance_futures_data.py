#!/usr/bin/env python3
"""Download public Binance USD-M futures candles and funding history to CSV.

No API key is required for public market data. This script only downloads data;
it does not create signals or claim a strategy backtest. Timestamps are UTC
candle OPEN times, and the last still-open candle is excluded by default.
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

BASE = "https://fapi.binance.com"
INTERVALS = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w", "1M"}


def parse_utc(value):
    if value.isdigit():
        return int(value)
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise argparse.ArgumentTypeError("Datetime must include timezone, e.g. 2025-01-01T00:00:00Z")
    return int(dt.timestamp() * 1000)


def get_json(path, params):
    url = BASE + path + "?" + urlencode(params)
    req = Request(url, headers={"User-Agent": "crypto-ai-trader-validation/1.0"})
    with urlopen(req, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_klines(symbol, interval, start_ms, end_ms, keep_open=False):
    out, cursor = [], start_ms
    while cursor < end_ms:
        batch = get_json("/fapi/v1/klines", {
            "symbol": symbol, "interval": interval, "startTime": cursor,
            "endTime": end_ms, "limit": 1500,
        })
        if not batch:
            break
        out.extend(batch)
        next_cursor = int(batch[-1][0]) + 1
        if next_cursor <= cursor:
            raise RuntimeError("Exchange pagination did not advance; aborting to avoid infinite loop")
        cursor = next_cursor
        if len(batch) < 1500:
            break
        time.sleep(0.15)
    # Deduplicate by candle open timestamp and enforce requested interval.
    dedup = {int(row[0]): row for row in out}
    now_ms = int(time.time() * 1000)
    rows = [dedup[k] for k in sorted(dedup) if start_ms <= k < end_ms]
    if not keep_open:
        rows = [r for r in rows if int(r[6]) < now_ms]
    return rows


def fetch_funding(symbol, start_ms, end_ms):
    out, cursor = [], start_ms
    while cursor < end_ms:
        batch = get_json("/fapi/v1/fundingRate", {
            "symbol": symbol, "startTime": cursor, "endTime": end_ms, "limit": 1000,
        })
        if not batch:
            break
        out.extend(batch)
        next_cursor = int(batch[-1]["fundingTime"]) + 1
        if next_cursor <= cursor:
            raise RuntimeError("Funding pagination did not advance")
        cursor = next_cursor
        if len(batch) < 1000:
            break
        time.sleep(0.15)
    dedup = {int(r["fundingTime"]): r for r in out}
    return [dedup[k] for k in sorted(dedup)]


def write_csv(path, headers, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbol", required=True, help="e.g. BTCUSDT")
    p.add_argument("--interval", default="1h", choices=sorted(INTERVALS))
    p.add_argument("--start", required=True, help="UTC ISO-8601, or Unix epoch milliseconds")
    p.add_argument("--end", required=True, help="UTC ISO-8601, or Unix epoch milliseconds")
    p.add_argument("--out-dir", default="data/raw")
    p.add_argument("--include-open-candle", action="store_true")
    p.add_argument("--funding", action="store_true", help="Also download funding history (separate CSV)")
    args = p.parse_args()
    symbol = args.symbol.upper()
    start_ms, end_ms = parse_utc(args.start), parse_utc(args.end)
    if start_ms >= end_ms:
        p.error("--start must be earlier than --end")
    safe_symbol = "".join(ch for ch in symbol if ch.isalnum())
    if safe_symbol != symbol:
        p.error("Invalid symbol")
    out_dir = Path(args.out_dir)
    candles = fetch_klines(symbol, args.interval, start_ms, end_ms, args.include_open_candle)
    candle_path = out_dir / f"{symbol}_{args.interval}_candles.csv"
    write_csv(candle_path,
              ["timestamp", "open", "high", "low", "close", "volume",
               "close_time_ms", "quote_volume", "trade_count", "taker_buy_base",
               "taker_buy_quote"],
              [[r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10]]
               for r in candles])
    report = {"symbol": symbol, "interval": args.interval, "candles": len(candles),
              "first_open_time_ms": candles[0][0] if candles else None,
              "last_open_time_ms": candles[-1][0] if candles else None,
              "candles_csv": str(candle_path), "funding_rows": None}
    if args.funding:
        funding = fetch_funding(symbol, start_ms, end_ms)
        funding_path = out_dir / f"{symbol}_funding.csv"
        write_csv(funding_path, ["funding_time_ms", "funding_rate", "mark_price"],
                  [[r["fundingTime"], r["fundingRate"], r.get("markPrice", "")] for r in funding])
        report["funding_rows"] = len(funding)
        report["funding_csv"] = str(funding_path)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
