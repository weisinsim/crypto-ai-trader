"""Paginated downloader for Binance USD-M historical funding rates."""
from __future__ import annotations

import argparse
from bisect import bisect_right
import csv
import os
import tempfile
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


class BinanceFundingAccessRestricted(RuntimeError):
    """Binance Futures REST funding endpoint is blocked for this runner/network."""


def canonical_settlement_timestamp(ts: int, tolerance_ms: int = 1000) -> int:
    """Normalize small timestamp jitter to the canonical 8-hour UTC settlement grid.

    The mark price must still come from a candle whose OPEN timestamp exactly
    matches this normalized settlement boundary.
    """
    # bool is a subclass of int in Python, but True/False are not valid
    # exchange timestamps or tolerances. Reject them explicitly to fail closed.
    if (isinstance(ts, bool) or not isinstance(ts, int) or ts < 0
            or isinstance(tolerance_ms, bool) or not isinstance(tolerance_ms, int)
            or tolerance_ms < 0):
        raise ValueError("timestamp and tolerance must be non-negative integers")
    interval_ms = 8 * 60 * 60 * 1000
    canonical = ((ts + interval_ms // 2) // interval_ms) * interval_ms
    if abs(canonical - ts) > tolerance_ms:
        raise ValueError(f"funding timestamp is off the canonical 8-hour grid: {ts}")
    return canonical

def _find_prior_mark_close(mark_closes: list[tuple[int, float]], settlement_ts: int) -> float | None:
    """Return the latest prior minute close only when it is at most 60 seconds old."""
    if not mark_closes:
        return None
    index = bisect_right(mark_closes, (settlement_ts, float("inf"))) - 1
    if index < 0:
        return None
    close_ts, close_price = mark_closes[index]
    if 0 <= settlement_ts - close_ts <= 60_000:
        return close_price
    return None


def _parse_archive_timestamp(value, field: str = "archive timestamp") -> int:
    """Parse an archive timestamp without float rounding or truncation."""
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer timestamp")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.strip().isascii() and value.strip().isdigit():
        parsed = int(value.strip())
    else:
        raise ValueError(f"{field} must be an integer timestamp")
    if parsed < 0:
        raise ValueError(f"{field} cannot be negative")
    return parsed


def _parse_integer_timestamp(value, field: str = "fundingTime") -> int:
    """Parse an exchange timestamp without silently truncating malformed values."""
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer timestamp")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isascii() and value.isdigit():
        return int(value)
    raise ValueError(f"{field} must be an integer timestamp")


def _parse_finite_number(value, field: str = "numeric value", *, positive: bool = False) -> float:
    """Parse finite archive numerics and optionally require a strictly positive value."""
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite number")
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{field} must be a finite number") from exc
    if not isfinite(parsed) or (positive and parsed <= 0):
        qualifier = "finite and positive" if positive else "finite"
        raise ValueError(f"{field} must be {qualifier}")
    return parsed


def parse_funding_rows(raw_rows: list[dict], symbol: str, start_ms: int, end_ms: int) -> list[dict]:
    if not isinstance(symbol, str) or not symbol.strip() or not symbol.strip().isalnum():
        raise ValueError("symbol must be a non-empty alphanumeric exchange symbol")
    if (isinstance(start_ms, bool) or not isinstance(start_ms, int)
            or isinstance(end_ms, bool) or not isinstance(end_ms, int)
            or start_ms < 0 or end_ms <= start_ms):
        raise ValueError("require integer timestamps with 0 <= start_ms < end_ms")
    if not isinstance(raw_rows, list):
        raise ValueError("funding response must be a list of rows")
    rows = []
    for index, raw in enumerate(raw_rows):
        if not isinstance(raw, dict):
            raise ValueError(f"funding row {index} must be a mapping")
        missing = [field for field in ("fundingTime", "fundingRate", "markPrice") if field not in raw]
        if missing:
            raise ValueError(f"funding row {index} missing required fields: {missing}")
        raw_symbol = raw.get("symbol")
        if raw_symbol is not None and str(raw_symbol).strip().upper() != symbol.upper():
            raise ValueError(
                f"funding response symbol mismatch at {raw.get('fundingTime')}: "
                f"expected {symbol.upper()}, got {str(raw_symbol).strip().upper()}"
            )
        ts = _parse_integer_timestamp(raw["fundingTime"])
        if isinstance(raw.get("fundingRate"), bool) or isinstance(raw.get("markPrice"), bool):
            raise ValueError(f"invalid funding rate or mark price at {ts}")
        try:
            rate = float(raw["fundingRate"])
            mark_price = float(raw["markPrice"])
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"invalid funding rate or mark price at {ts}") from exc
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
    # Reject bool explicitly: Python treats bool as int, which can silently
    # turn malformed range/limit inputs into valid-looking requests.
    if (isinstance(start_ms, bool) or not isinstance(start_ms, int)
            or isinstance(end_ms, bool) or not isinstance(end_ms, int)
            or start_ms < 0 or end_ms <= start_ms):
        raise ValueError("require integer timestamps with 0 <= start_ms < end_ms")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError("limit must be an integer between 1 and 1000")
    if isinstance(pause_seconds, bool) or not isinstance(pause_seconds, (int, float)) or not isfinite(pause_seconds) or pause_seconds < 0:
        raise ValueError("pause_seconds must be a finite non-negative number")

    client = None
    if fetch_page is None:
        client = httpx.Client(timeout=15.0)

        def fetch_page(params):
            url = base_url.rstrip("/") + "/fapi/v1/fundingRate"
            response = client.get(url, params=params)
            if response.status_code == 451:
                raise BinanceFundingAccessRestricted(
                    "Binance USD-M funding REST endpoint returned HTTP 451 "
                    f"({response.url}). This runner/network is restricted by the "
                    "upstream service; retries and changing the date range will not "
                    "reliably fix a jurisdiction/access block. Use an authorized "
                    "network or a verifiable official Binance Vision archive. "
                    "Do not synthesize funding records or treat missing funding as zero."
                )
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
            page_timestamps = [_parse_integer_timestamp(row["fundingTime"]) for row in page]
            # A page can overlap the previous page. Preserve duplicate detection
            # across pages (including conflicting duplicates) instead of masking
            # it with the range guard. A page containing only stale timestamps
            # cannot advance pagination and must fail immediately.
            outside_range = [ts for ts in page_timestamps if ts < start_ms or ts >= end_ms]
            if outside_range:
                raise ValueError(
                    f"funding API returned timestamps outside requested page range "
                    f"[{start_ms}, {end_ms}): {outside_range[:5]}"
                )
            forward_timestamps = [ts for ts in page_timestamps if ts >= cursor]
            if not forward_timestamps:
                raise RuntimeError("funding pagination did not advance")
            records.extend(parsed)
            last_ts = max(forward_timestamps)
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
        timestamp = record["funding_time"]
        if timestamp in by_time:
            if by_time[timestamp] != record:
                raise ValueError(f"conflicting duplicate funding record at {timestamp}")
            raise ValueError(f"duplicate funding record at {timestamp}")
        by_time[timestamp] = record
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
    if not isinstance(symbol, str) or not symbol.strip() or not symbol.strip().isalnum():
        raise ValueError("symbol must be a non-empty alphanumeric exchange symbol")
    if (isinstance(start_ms, bool) or not isinstance(start_ms, int)
            or isinstance(end_ms, bool) or not isinstance(end_ms, int)
            or start_ms < 0 or end_ms <= start_ms):
        raise ValueError("require integer timestamps with 0 <= start_ms < end_ms")
    try:
        datetime.fromtimestamp(start_ms / 1000, timezone.utc)
        datetime.fromtimestamp((end_ms - 1) / 1000, timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise ValueError("timestamp range is outside the supported UTC datetime range") from exc
    symbol = symbol.strip().upper()
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
                    datetime.fromtimestamp(_parse_archive_timestamp(row[0]) / 1000, timezone.utc).strftime("%Y-%m-%d")
                    for row in rate_rows if row and row[0].strip().isdigit()
                    and start_ms <= _parse_archive_timestamp(row[0]) < end_ms
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
                    ts = _parse_archive_timestamp(row[0])
                except (ValueError, IndexError):
                    continue
                mark_open = _parse_finite_number(row[1], "archive mark price", positive=True)
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
                    funding_ts = canonical_settlement_timestamp(_parse_archive_timestamp(funding_row[0]))
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
                            minute_open_ts = _parse_archive_timestamp(item[0])
                        except (ValueError, IndexError):
                            continue
                        mark_open = _parse_finite_number(item[1], "1m archive mark open", positive=True)
                        mark_close = _parse_finite_number(item[4], "1m archive mark close", positive=True)
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
                                minute_open_ts = _parse_archive_timestamp(item[0])
                            except (ValueError, IndexError):
                                continue
                            mark_open = _parse_finite_number(item[1], "daily 1m archive mark open", positive=True)
                            mark_close = _parse_finite_number(item[4], "daily 1m archive mark close", positive=True)
                            mark_1m_opens[minute_open_ts] = mark_open
                            mark_1m_closes.append((minute_open_ts + 60_000, mark_close))
                mark_1m_closes.sort()
            for row in rate_rows:
                try:
                    ts = canonical_settlement_timestamp(_parse_archive_timestamp(row[0]))
                except (ValueError, IndexError):
                    continue
                rate = _parse_finite_number(row[-1], "archive funding rate")
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
                        # Secondary fallback is historical only and limited to one minute.
                        mark = _find_prior_mark_close(mark_1m_closes, ts)
                        if mark is not None:
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
    """Write funding records atomically so failed writes cannot truncate prior data."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, destination)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


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
    except (httpx.HTTPStatusError, BinanceFundingAccessRestricted):
        # The official public archive is independent of the jurisdiction-restricted REST host.
        rows = download_archive_funding(args.symbol, args.start_ms, args.end_ms)
        source = "binance_vision_archive"
    save_funding_csv(rows, args.output)
    print(f"status=COMPLETE source={source} symbol={args.symbol.upper()} records={len(rows)} output={args.output}")


if __name__ == "__main__":
    main()
