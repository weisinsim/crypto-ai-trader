#!/usr/bin/env python3
"""Download public Binance USD-M Futures OHLCV candles for research.

Writes CSVs whose timestamp is candle CLOSE time in UTC ISO-8601, matching
validation/exit_parameter_audit.py's signal timestamp contract. Public market
data only; no API key or trading permission is required.

Example:
  python validation/download_futures_klines.py --symbols BTCUSDT ETHUSDT SOLUSDT AVAXUSDT XRPUSDT --interval 1h --start 2024-10-01 --end 2026-10-01 --out-dir data/historical

Use only fully closed candles. The end date is exclusive. Check exchange
availability and verify file completeness before any performance conclusions.
"""
import argparse
import csv
import json
import io
import sys
import zipfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = "https://fapi.binance.com/fapi/v1/klines"
LIMIT = 1500
INTERVAL_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
    "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000,
    "4h": 14_400_000, "6h": 21_600_000, "8h": 28_800_000,
    "12h": 43_200_000, "1d": 86_400_000,
}


def utc_ms(date_str):
    dt = datetime.fromisoformat(date_str)
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
            # Keep the exchange response body visible in hosted CI logs.
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:1000]
            except Exception:
                detail = "(response body unavailable)"
            if exc.code not in (418, 429, 500, 502, 503, 504) or attempt == retries - 1:
                raise RuntimeError(
                    f"Binance kline HTTP {exc.code}; endpoint={BASE}; response={detail}"
                ) from exc
            time.sleep(min(2 ** attempt, 30))
        except (URLError, TimeoutError) as exc:
            if attempt == retries - 1:
                raise RuntimeError(f"Could not reach Binance kline endpoint {BASE}: {exc}") from exc
            time.sleep(min(2 ** attempt, 30))
    raise RuntimeError("Retry budget exhausted")


def download_archive(symbol, interval, start_ms, end_ms, now_ms=None):
    """Read Binance's public data archive when the REST API is geo-restricted.

    Monthly archives are used for completed months and daily archives for the
    current/incomplete month. This is an official public market-data mirror;
    it does not bypass trading controls or use authenticated endpoints.
    """
    from calendar import monthrange
    from urllib.error import HTTPError
    import io
    import zipfile

    if now_ms is None:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).date()
    end = datetime.fromtimestamp((end_ms - 1) / 1000, tz=timezone.utc).date()
    rows = []
    month_cursor = start.replace(day=1)
    while month_cursor <= end:
        month_end_day = monthrange(month_cursor.year, month_cursor.month)[1]
        month_end = month_cursor.replace(day=month_end_day)
        # Current month is not guaranteed to have a complete monthly archive.
        use_daily = month_cursor.year == datetime.now(timezone.utc).year and month_cursor.month == datetime.now(timezone.utc).month
        dates = []
        if use_daily:
            d = max(start, month_cursor)
            last = min(end, month_end)
            while d <= last:
                dates.append(("daily", d))
                d = d.fromordinal(d.toordinal() + 1)
        else:
            dates.append(("monthly", month_cursor))
        for kind, period in dates:
            stamp = period.strftime("%Y-%m-%d") if kind == "daily" else period.strftime("%Y-%m")
            url = (f"https://data.binance.vision/data/futures/um/{kind}/klines/"
                   f"{symbol}/{interval}/{symbol}-{interval}-{stamp}.zip")
            try:
                req = Request(url, headers={"User-Agent": "crypto-ai-trader-research/1.0"})
                with urlopen(req, timeout=30) as response:
                    archive_bytes = response.read()
            except HTTPError as exc:
                if exc.code == 404:
                    raise RuntimeError(f"Official Binance archive missing: {url}") from exc
                raise RuntimeError(f"Binance archive HTTP {exc.code}: {url}") from exc
            with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
                names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
                if not names:
                    raise RuntimeError(f"No CSV found in archive {url}")
                reader = csv.reader(io.TextIOWrapper(archive.open(names[0]), encoding="utf-8"))
                for item in reader:
                    if not item or not item[0].isdigit():
                        continue
                    open_ms, close_ms = int(item[0]), int(item[6])
                    if open_ms < start_ms or close_ms >= end_ms or close_ms >= now_ms:
                        continue
                    rows.append({
                        "timestamp": iso_utc(close_ms), "open": item[1], "high": item[2],
                        "low": item[3], "close": item[4], "volume": item[5],
                        "quote_volume": item[7] if len(item) > 7 else "",
                        "trade_count": item[8] if len(item) > 8 else "",
                        "taker_buy_base_volume": item[9] if len(item) > 9 else "",
                        "taker_buy_quote_volume": item[10] if len(item) > 10 else "",
                    })
        month_cursor = month_cursor.replace(day=1)
        if month_cursor.month == 12:
            month_cursor = month_cursor.replace(year=month_cursor.year + 1, month=1)
        else:
            month_cursor = month_cursor.replace(month=month_cursor.month + 1)
    by_ts = {row["timestamp"]: row for row in rows}
    return [by_ts[k] for k in sorted(by_ts)]


def download(symbol, interval, start_ms, end_ms, now_ms=None):
    rows, cursor = [], start_ms
    step = INTERVAL_MS[interval]
    # Binance kline close_time is inclusive (e.g. ...:59.999). Do not persist
    # any candle whose close timestamp is not strictly before the current time.
    if now_ms is None:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    while cursor < end_ms:
        batch = get_json({
            "symbol": symbol,
            "interval": interval,
            "startTime": cursor,
            "endTime": end_ms - 1,
            "limit": LIMIT,
        })
        if not batch:
            break
        for item in batch:
            open_ms, close_ms = int(item[0]), int(item[6])
            if open_ms < start_ms or close_ms >= end_ms or close_ms >= now_ms:
                continue
            rows.append({
                "timestamp": iso_utc(close_ms),
                "open": item[1], "high": item[2], "low": item[3], "close": item[4],
                "volume": item[5], "quote_volume": item[7], "trade_count": item[8],
                "taker_buy_base_volume": item[9], "taker_buy_quote_volume": item[10],
            })
        next_cursor = int(batch[-1][0]) + step
        if next_cursor <= cursor:
            raise RuntimeError(f"Pagination did not advance for {symbol} {interval}")
        cursor = next_cursor
        if len(batch) < LIMIT:
            break
        time.sleep(0.15)
    # De-duplicate and sort in case an exchange response repeats a boundary candle.
    by_ts = {row["timestamp"]: row for row in rows}
    return [by_ts[k] for k in sorted(by_ts)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT", "XRPUSDT"])
    parser.add_argument("--interval", choices=sorted(INTERVAL_MS), default="1h")
    parser.add_argument("--start", required=True, help="UTC start date, e.g. 2024-10-01")
    parser.add_argument("--end", required=True, help="UTC exclusive end date, e.g. 2026-10-01")
    parser.add_argument("--out-dir", default="data/historical")
    args = parser.parse_args()
    start_ms, end_ms = utc_ms(args.start), utc_ms(args.end)
    if start_ms >= end_ms:
        parser.error("--start must be earlier than --end")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = []
    for symbol in args.symbols:
        try:
            try:
                rows = download(symbol.upper(), args.interval, start_ms, end_ms)
            except RuntimeError as exc:
                if "HTTP 451" not in str(exc):
                    raise
                print(
                    f"REST API is region-restricted; trying official Binance public archive "
                    f"for {symbol.upper()} {args.interval}.",
                    file=sys.stderr, flush=True,
                )
                rows = download_archive(symbol.upper(), args.interval, start_ms, end_ms)
        except Exception as exc:
            print(
                f"ERROR downloading symbol={symbol.upper()} interval={args.interval} "
                f"start={args.start} end_exclusive={args.end}: {exc}",
                file=sys.stderr,
                flush=True,
            )
            raise
        path = out_dir / f"{symbol.upper()}_{args.interval}.csv"
        fields = ["timestamp", "open", "high", "low", "close", "volume", "quote_volume",
                  "trade_count", "taker_buy_base_volume", "taker_buy_quote_volume"]
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        expected = max(0, (end_ms - start_ms) // INTERVAL_MS[args.interval])
        report.append({
            "symbol": symbol.upper(), "interval": args.interval, "rows": len(rows),
            "expected_approx_rows": expected, "first_close_utc": rows[0]["timestamp"] if rows else None,
            "last_close_utc": rows[-1]["timestamp"] if rows else None, "file": str(path),
            "warning": "Inspect gaps and compare expected rows before backtesting."
        })
        print(json.dumps(report[-1]))
    (out_dir / f"download_manifest_{args.interval}.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
