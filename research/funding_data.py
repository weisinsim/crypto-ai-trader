"""Paginated downloader for Binance USD-M historical funding rates."""
from __future__ import annotations

import argparse
import csv
import io
import time
import zipfile
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
from typing import Callable

import httpx

BASE_URL = "https://fapi.binance.com"
FIELDS = ("funding_time", "funding_rate", "mark_price", "symbol", "mark_price_source", "venue")


def canonical_settlement_timestamp(ts: int, tolerance_ms: int = 1000) -> int:
    """Normalize small timestamp jitter to the canonical 8-hour UTC settlement grid.

    The mark price must still come from a candle whose OPEN timestamp exactly
    matches this normalized settlement boundary.
    """
    if not isinstance(ts, int) or ts < 0 or not isinstance(tolerance_ms, int) or tolerance_ms < 0:
        raise ValueError("timestamp and tolerance must be non-negative integers")
    interval_ms = 8 * 60 * 60 * 1000
    canonical = ((ts + interval_ms // 2) // interval_ms) * interval_ms
    if abs(canonical - ts) > tolerance_ms:
        raise ValueError(f"funding timestamp is off the canonical 8-hour grid: {ts}")
    return canonical

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
                         "mark_price": mark_price, "symbol": symbol.upper(),
                         "mark_price_source": "binance_fapi_funding_rate_mark_price", "venue": "binance"})
    rows.sort(key=lambda row: row["funding_time"])
    for left, right in zip(rows, rows[1:]):
        if left["funding_time"] == right["funding_time"]:
            raise ValueError(f"duplicate funding timestamp: {left['funding_time']}")
    return rows


def download_funding_rates(
    symbol: str, start_ms: int, end_ms: int, *,
    fetch_page: Callable | None = None, limit: int = 1000, pause_seconds: float = 0.05,
    base_url: str = BASE_URL,
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
            response = client.get(base_url.rstrip("/") + "/fapi/v1/fundingRate", params=params)
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


def _month_starts(start_ms: int, end_ms: int):
    start = datetime.fromtimestamp(start_ms / 1000, timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = datetime.fromtimestamp(end_ms / 1000, timezone.utc)
    current = start
    while current < end:
        yield current
        current = current.replace(year=current.year + (current.month == 12), month=1 if current.month == 12 else current.month + 1)


def download_archive_funding(symbol: str, start_ms: int, end_ms: int) -> list[dict]:
    """Fetch monthly Binance Vision fundingRate and 8h mark-price archives.

    REST funding endpoints can be jurisdiction-blocked. The public archive
    provides rates; markPriceKlines supplies the 8h settlement-price proxy.
    """
    symbol = symbol.upper()
    rows = []
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        for month in _month_starts(start_ms, end_ms):
            ym = month.strftime("%Y-%m")
            rate_url = f"https://data.binance.vision/data/futures/um/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{ym}.zip"
            mark_url = f"https://data.binance.vision/data/futures/um/monthly/markPriceKlines/{symbol}/8h/{symbol}-8h-{ym}.zip"
            rate_resp = client.get(rate_url)
            if rate_resp.status_code == 404:
                # Current-month monthly archives may not be published yet.
                # Try Binance's public data REST hostname before archive fallbacks;
                # the primary futures host can return HTTP 451 in some CI regions.
                month_start_ms = max(start_ms, int(month.timestamp() * 1000))
                next_month = month.replace(
                    year=month.year + (month.month == 12),
                    month=1 if month.month == 12 else month.month + 1,
                )
                month_end_ms = min(end_ms, int(next_month.timestamp() * 1000))
                # data-api.binance.vision does not expose the USD-M Futures
                # fundingRate endpoint (it returns 404); do not treat it as a
                # valid fallback. Keep the failure explicit until a supported
                # official futures endpoint/archive is available.
                mirror_error = "unsupported endpoint: data-api.binance.vision/fapi/v1/fundingRate (HTTP 404)"
                mirror_rows = []
                if mirror_rows:
                    rows.extend(mirror_rows)
                    continue
                # Assemble the requested portion from official daily fundingRate
                # archives; fail closed if neither official source is available.
                month_start_ms = max(start_ms, int(month.timestamp() * 1000))
                next_month = month.replace(
                    year=month.year + (month.month == 12),
                    month=1 if month.month == 12 else month.month + 1,
                )
                month_end_ms = min(end_ms, int(next_month.timestamp() * 1000))
                day = datetime.fromtimestamp(month_start_ms / 1000, timezone.utc).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                daily_rate_rows = []
                while int(day.timestamp() * 1000) < month_end_ms:
                    date_str = day.strftime("%Y-%m-%d")
                    daily_url = (
                        f"https://data.binance.vision/data/futures/um/daily/fundingRate/"
                        f"{symbol}/{symbol}-fundingRate-{date_str}.zip"
                    )
                    daily_resp = client.get(daily_url)
                    if daily_resp.status_code == 200:
                        with zipfile.ZipFile(io.BytesIO(daily_resp.content)) as zf:
                            daily_name = next((n for n in zf.namelist() if n.lower().endswith(".csv")), None)
                            if not daily_name:
                                raise ValueError(f"daily funding archive has no CSV: {daily_url}")
                            daily_rate_rows.extend(csv.reader(io.TextIOWrapper(zf.open(daily_name), encoding="utf-8")))
                    elif daily_resp.status_code != 404:
                        daily_resp.raise_for_status()
                    day = day.replace(day=day.day) + __import__("datetime").timedelta(days=1)
                if not daily_rate_rows:
                    # Final fallback: the official USD-M Futures funding history
                    # endpoint returns both fundingRate and the settlement markPrice.
                    # Keep the request scoped to the missing month and preserve provenance.
                    try:
                        rest_rows = download_funding_rates(
                            symbol, month_start_ms, month_end_ms, base_url=BASE_URL
                        )
                    except Exception as exc:
                        raise ValueError(
                            f"funding data unavailable for {symbol} in {ym}: monthly "
                            f"archive missing ({rate_url}), daily archives unavailable, "
                            f"official REST fallback failed ({type(exc).__name__}: {exc}); "
                            "refusing to synthesize records"
                        ) from exc
                    if not rest_rows:
                        raise ValueError(
                            f"official REST returned no funding records for {symbol} "
                            f"in [{month_start_ms}, {month_end_ms}); refusing to synthesize records"
                        )
                    rows.extend(rest_rows)
                    continue
                # Build an in-memory CSV archive so the shared mark-price
                # provenance checks below apply identically to monthly and daily data.
                csv_buffer = io.StringIO()
                csv.writer(csv_buffer).writerows(daily_rate_rows)
                archive_buffer = io.BytesIO()
                with zipfile.ZipFile(archive_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                    zf.writestr("daily-funding.csv", csv_buffer.getvalue())
                rate_payload = archive_buffer.getvalue()
            else:
                rate_resp.raise_for_status()
                rate_payload = rate_resp.content
            mark_resp = client.get(mark_url)
            if mark_resp.status_code == 404:
                # Current month 8h mark-price archives may also be unpublished.
                mark_payload = None
            else:
                mark_resp.raise_for_status()
                mark_payload = mark_resp.content
            with zipfile.ZipFile(io.BytesIO(rate_payload)) as zf:
                rate_name = next((n for n in zf.namelist() if n.lower().endswith(".csv")), None)
                if not rate_name:
                    raise ValueError(f"funding archive has no CSV: {rate_url}")
                rate_rows = list(csv.reader(io.TextIOWrapper(zf.open(rate_name), encoding="utf-8")))
            mark_rows = []
            if mark_payload is not None:
                with zipfile.ZipFile(io.BytesIO(mark_payload)) as zf:
                    mark_name = next((n for n in zf.namelist() if n.lower().endswith(".csv")), None)
                    if not mark_name:
                        raise ValueError(f"mark-price archive has no CSV: {mark_url}")
                    mark_rows = list(csv.reader(io.TextIOWrapper(zf.open(mark_name), encoding="utf-8")))
            else:
                # Fetch daily 1m official mark-price archives for current-month
                # settlement dates. Later fallback logic verifies each timestamp.
                for date_str in sorted({
                    datetime.fromtimestamp(int(float(row[0])) / 1000, timezone.utc).strftime("%Y-%m-%d")
                    for row in rate_rows if row and row[0].strip().replace(".", "", 1).isdigit()
                    and start_ms <= int(float(row[0])) < end_ms
                }):
                    daily_url = (
                        f"https://data.binance.vision/data/futures/um/daily/markPriceKlines/"
                        f"{symbol}/1m/{symbol}-1m-{date_str}.zip"
                    )
                    daily_resp = client.get(daily_url)
                    if daily_resp.status_code == 404:
                        continue
                    daily_resp.raise_for_status()
                    with zipfile.ZipFile(io.BytesIO(daily_resp.content)) as zf:
                        daily_name = next((n for n in zf.namelist() if n.lower().endswith(".csv")), None)
                        if not daily_name:
                            raise ValueError(f"daily mark-price archive has no CSV: {daily_url}")
                        mark_rows.extend(csv.reader(io.TextIOWrapper(zf.open(daily_name), encoding="utf-8")))
            marks = {}
            for row in mark_rows:
                try:
                    ts, mark_open = int(float(row[0])), float(row[1])
                except (ValueError, IndexError):
                    continue
                # Use the mark-price candle OPEN at settlement time, not its
                # close eight hours later (which would introduce look-ahead).
                marks[ts] = mark_open
            # If an 8h mark-price archive lacks a settlement timestamp, retry
            # with the official 1m mark-price archive for the same month. Never
            # substitute a trade-kline close for an exchange mark price.
            mark_1m_closes = []
            mark_1m_opens = {}
            missing_8h_times = set()
            for funding_row in rate_rows:
                try:
                    funding_ts = canonical_settlement_timestamp(int(float(funding_row[0])))
                except (ValueError, IndexError):
                    continue
                if not (start_ms <= funding_ts < end_ms):
                    continue
                if funding_ts in marks:
                    continue
                if any(abs(mark_ts - funding_ts) <= 1000 for mark_ts in marks):
                    continue
                missing_8h_times.add(funding_ts)
            if missing_8h_times:
                # Try the monthly archive first. If it is truncated or has gaps,
                # fetch daily official mark-price archives for the specific missing
                # settlement dates instead of widening the time tolerance.
                mark_1m_url = (
                    f"https://data.binance.vision/data/futures/um/monthly/markPriceKlines/"
                    f"{symbol}/1m/{symbol}-1m-{ym}.zip"
                )
                mark_1m_resp = client.get(mark_1m_url)
                mark_1m_resp.raise_for_status()
                with zipfile.ZipFile(io.BytesIO(mark_1m_resp.content)) as zf:
                    mark_1m_name = next((n for n in zf.namelist() if n.lower().endswith(".csv")), None)
                    if not mark_1m_name:
                        raise ValueError(f"1m mark-price archive has no CSV: {mark_1m_url}")
                    mark_1m_rows = csv.reader(io.TextIOWrapper(zf.open(mark_1m_name), encoding="utf-8"))
                    for item in mark_1m_rows:
                        try:
                            minute_open_ts = int(float(item[0]))
                            mark_open, mark_close = float(item[1]), float(item[4])
                        except (ValueError, IndexError):
                            continue
                        mark_1m_opens[minute_open_ts] = mark_open
                        mark_1m_closes.append((minute_open_ts + 60_000, mark_close))

                unresolved_dates = set()
                for funding_ts in missing_8h_times:
                    if funding_ts not in mark_1m_opens and not any(
                        abs(mark_ts - funding_ts) <= 1000 for mark_ts in mark_1m_opens
                    ):
                        unresolved_dates.add(datetime.fromtimestamp(
                            funding_ts / 1000, timezone.utc
                        ).strftime("%Y-%m-%d"))
                for date_str in sorted(unresolved_dates):
                    daily_url = (
                        f"https://data.binance.vision/data/futures/um/daily/markPriceKlines/"
                        f"{symbol}/1m/{symbol}-1m-{date_str}.zip"
                    )
                    daily_resp = client.get(daily_url)
                    if daily_resp.status_code == 404:
                        continue
                    daily_resp.raise_for_status()
                    with zipfile.ZipFile(io.BytesIO(daily_resp.content)) as zf:
                        daily_name = next((n for n in zf.namelist() if n.lower().endswith(".csv")), None)
                        if not daily_name:
                            raise ValueError(f"daily 1m mark-price archive has no CSV: {daily_url}")
                        daily_rows = csv.reader(io.TextIOWrapper(zf.open(daily_name), encoding="utf-8"))
                        for item in daily_rows:
                            try:
                                minute_open_ts = int(float(item[0]))
                                mark_open, mark_close = float(item[1]), float(item[4])
                            except (ValueError, IndexError):
                                continue
                            mark_1m_opens[minute_open_ts] = mark_open
                            mark_1m_closes.append((minute_open_ts + 60_000, mark_close))
                mark_1m_closes.sort()
                mark_1m_closes.sort()
            for row in rate_rows:
                try:
                    ts = canonical_settlement_timestamp(int(float(row[0])))
                    rate = float(row[-1])
                except (ValueError, IndexError):
                    continue
                if start_ms <= ts < end_ms:
                    # Settlement records align to the open of the 8h mark-price candle.
                    mark = marks.get(ts)
                    mark_source = "mark_kline_open"
                    if mark is None and mark_1m_opens:
                        # Timestamp is normalized to the canonical settlement boundary;
                        # require an exact mark-price candle OPEN match.
                        mark = mark_1m_opens.get(ts)
                        if mark is not None:
                            mark_source = "mark_kline_1m_open"
                    if mark is None and mark_1m_closes:
                        prior = [item for item in mark_1m_closes if item[0] <= ts]
                        # Secondary fallback is historical only and limited to one minute.
                        if prior and 0 <= ts - prior[-1][0] <= 60_000:
                            mark = prior[-1][1]
                            mark_source = "mark_kline_1m_prior_close"
                    if mark is None:
                        nearest_1m_open = (
                            min(mark_1m_opens, key=lambda mark_ts: abs(mark_ts - ts))
                            if mark_1m_opens else None
                        )
                        nearest_1m_close = (
                            min(mark_1m_closes, key=lambda item: abs(item[0] - ts))
                            if mark_1m_closes else None
                        )
                        raise ValueError(
                            f"missing official mark price for {symbol} at funding timestamp {ts}; "
                            f"8h_mark_count={len(marks)}, 1m_mark_open_count={len(mark_1m_opens)}, "
                            f"nearest_1m_open_ts={nearest_1m_open}, "
                            f"nearest_1m_open_delta_ms="
                            f"{abs(nearest_1m_open - ts) if nearest_1m_open is not None else None}, "
                            f"nearest_1m_close={nearest_1m_close}; refusing trade-price proxy"
                        )
                    if not isfinite(rate) or not isfinite(mark) or mark <= 0:
                        raise ValueError(f"invalid funding rate or official mark price at {ts} for {symbol}")
                    rows.append({"funding_time": ts, "funding_rate": rate,
                                 "mark_price": mark, "symbol": symbol,
                                 "mark_price_source": mark_source, "venue": "binance"})
    rows.sort(key=lambda row: row["funding_time"])
    if not rows:
        raise ValueError(f"no funding archive records returned for {symbol}")
    for left, right in zip(rows, rows[1:]):
        if left["funding_time"] == right["funding_time"]:
            raise ValueError(f"duplicate funding timestamp: {left['funding_time']}")
    return rows


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
    try:
        rows = download_funding_rates(args.symbol, args.start_ms, args.end_ms)
        source = "binance_rest"
    except httpx.HTTPStatusError:
        # The official public archive is independent of the jurisdiction-restricted REST host.
        rows = download_archive_funding(args.symbol, args.start_ms, args.end_ms)
        source = "binance_vision_archive"
    save_funding_csv(rows, args.output)
    print(f"status=COMPLETE source={source} symbol={args.symbol.upper()} records={len(rows)} output={args.output}")


if __name__ == "__main__":
    main()
