#!/usr/bin/env python3
"""Download public Binance USD-M Futures funding-rate history.

Output schema is timestamp,funding_rate where timestamp is the funding event
time in UTC and funding_rate is the decimal rate (0.0001 = 0.01%).
"""
import argparse
import csv
import json
import io
import time
import zipfile
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


def parse_archive_row(item, symbol, start_ms, end_ms):
    """Parse Binance Vision rows with or without interval-hours column."""
    if not item or len(item) < 2 or not item[0].isdigit():
        return None
    ts = int(item[0])
    if not start_ms <= ts < end_ms:
        return None
    try:
        rate = float(item[-1])
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Invalid funding rate in archive row for {symbol}: {item!r}") from exc
    import math
    if not math.isfinite(rate):
        raise RuntimeError(f"Non-finite funding rate in archive row for {symbol}: {item!r}")
    return {"timestamp": iso_utc(ts), "funding_rate": str(rate)}


def download_archive(symbol, start_ms, end_ms):
    """Fallback to Binance public funding-rate archives if REST is region-blocked."""
    from calendar import monthrange

    start = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).date()
    end = datetime.fromtimestamp((end_ms - 1) / 1000, tz=timezone.utc).date()
    today = datetime.now(timezone.utc).date()
    rows = []

    def read_archive(kind, period):
        stamp = period.strftime("%Y-%m-%d") if kind == "daily" else period.strftime("%Y-%m")
        url = (f"https://data.binance.vision/data/futures/um/{kind}/fundingRate/"
               f"{symbol}/{symbol}-fundingRate-{stamp}.zip")
        req = Request(url, headers={"User-Agent": "crypto-ai-trader-research/1.0"})
        try:
            with urlopen(req, timeout=30) as response:
                payload = response.read()
        except HTTPError as exc:
            if exc.code == 404:
                return False
            raise RuntimeError(f"Binance funding archive HTTP {exc.code}: {url}") from exc
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if not names:
                raise RuntimeError(f"No CSV found in funding archive {url}")
            with archive.open(names[0]) as raw:
                reader = csv.reader(io.TextIOWrapper(raw, encoding="utf-8"))
                for item in reader:
                    parsed = parse_archive_row(item, symbol, start_ms, end_ms)
                    if parsed is not None:
                        rows.append(parsed)
        return True

    month = start.replace(day=1)
    while month <= end:
        last_day = monthrange(month.year, month.month)[1]
        month_end = month.replace(day=last_day)
        current_month = month.year == today.year and month.month == today.month
        if not current_month and read_archive("monthly", month):
            pass
        else:
            day = max(start, month)
            last = min(end, month_end)
            while day <= last:
                if day <= today and not read_archive("daily", day):
                    raise RuntimeError(
                        f"Official Binance funding archive missing for {symbol} {day}; "
                        "cannot safely use incomplete funding history."
                    )
                day = day.fromordinal(day.toordinal() + 1)
        if month.month == 12:
            month = month.replace(year=month.year + 1, month=1)
        else:
            month = month.replace(month=month.month + 1)
    by_ts = {row["timestamp"]: row for row in rows}
    return [by_ts[key] for key in sorted(by_ts)]


def download(symbol, start_ms, end_ms):
    rows, cursor = [], start_ms
    try:
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
    except HTTPError as exc:
        if exc.code != 451:
            raise
        rows = download_archive(symbol, start_ms, end_ms)
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
