"""Fallback reader for Binance public USD-M archives.

Funding archives contain rates/timestamps, not settlement mark prices. Research
fallbacks use a prior closed mark-price candle when available, then a prior
closed regular-price candle as a lower-quality proxy. Neither is the actual
settlement mark; source labels must be retained and cashflows are not reconciled.
"""
from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from datetime import datetime, timezone
from typing import Iterable

import httpx

ARCHIVE = "https://data.binance.vision/data/futures/um"
HOUR_MS = 3_600_000


def _months(start_ms: int, end_ms: int) -> Iterable[str]:
    start = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
    end = datetime.fromtimestamp((end_ms - 1) / 1000, tz=timezone.utc)
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        yield f"{year:04d}-{month:02d}"
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def _archive_csv(client: httpx.Client, relative_path: str) -> list[list[str]]:
    url = f"{ARCHIVE}/{relative_path}"
    response = client.get(url, timeout=60)
    if response.status_code == 404:
        return []
    response.raise_for_status()
    archive_bytes = response.content

    # Verify against the publisher's checksum when it is available. A missing
    # checksum does not silently turn into a claimed verification.
    checksum_response = client.get(url + ".CHECKSUM", timeout=30)
    if checksum_response.status_code == 200:
        parts = checksum_response.text.strip().split()
        if parts:
            expected = parts[0].lower()
            actual = hashlib.sha256(archive_bytes).hexdigest()
            if actual != expected:
                raise ValueError(f"archive SHA-256 mismatch for {relative_path}")
    elif checksum_response.status_code not in (403, 404):
        checksum_response.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zipped:
        csv_names = [name for name in zipped.namelist() if name.lower().endswith(".csv")]
        if len(csv_names) != 1:
            raise ValueError(f"expected one CSV in archive {relative_path}, got {csv_names}")
        with zipped.open(csv_names[0]) as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
            rows = list(csv.reader(text))
    if rows and rows[0] and not rows[0][0].strip().lstrip("-").isdigit():
        rows = rows[1:]
    return rows


def _archive_rows(client: httpx.Client, symbol: str, data_type: str,
                  interval: str | None, start_ms: int, end_ms: int) -> list[list[str]]:
    result = []
    for ym in _months(start_ms, end_ms):
        if data_type in ("klines", "markPriceKlines"):
            assert interval is not None
            name = f"{symbol}-{interval}-{ym}.zip"
            path = f"monthly/{data_type}/{symbol}/{interval}/{name}"
        elif data_type == "fundingRate":
            name = f"{symbol}-fundingRate-{ym}.zip"
            path = f"monthly/fundingRate/{symbol}/{name}"
        else:
            raise ValueError(f"unsupported archive data type: {data_type}")
        result.extend(_archive_csv(client, path))
    return result


def download_archive_klines(symbol: str, interval: str, start_ms: int,
                            end_ms: int, interval_ms: int) -> list[dict]:
    """Download and strictly validate closed futures candles from monthly ZIPs."""
    with httpx.Client(follow_redirects=True) as client:
        raw = _archive_rows(client, symbol.upper(), "klines", interval, start_ms, end_ms)
    normalized = []
    for index, row in enumerate(raw):
        if len(row) < 8:
            raise ValueError(f"archive kline row {index} has fewer than 8 fields")
        # Archive columns match the futures kline endpoint's array layout.
        normalized.append(row)
    from research.binance_data import parse_klines
    parsed = parse_klines(normalized, interval_ms, end_ms)
    parsed = [r for r in parsed if start_ms <= r["t"] < end_ms]
    expected = list(range(start_ms, end_ms, interval_ms))
    actual = [r["t"] for r in parsed]
    if actual != expected:
        missing = sorted(set(expected) - set(actual))
        raise ValueError(
            f"archive incomplete coverage for {symbol} {interval}: expected {len(expected)}, "
            f"got {len(actual)}; missing_first={missing[:5]}"
        )
    return parsed


def download_archive_funding(symbol: str, start_ms: int, end_ms: int) -> list[dict]:
    """Download funding-rate archive; attach a clearly labelled prior mark-candle proxy."""
    symbol = symbol.upper()
    # Include the month containing the preceding candle. Archive URLs are monthly,
    # so requesting start_ms - 1h is sufficient even when start is month boundary.
    with httpx.Client(follow_redirects=True) as client:
        funding_raw = _archive_rows(client, symbol, "fundingRate", None, start_ms, end_ms)
        mark_raw = _archive_rows(client, symbol, "markPriceKlines", "1h",
                                 max(0, start_ms - HOUR_MS), end_ms)
        # Some symbols/months have no published markPriceKlines archive. Keep a
        # distinct regular-price fallback rather than failing immediately.
        regular_raw = _archive_rows(client, symbol, "klines", "1h",
                                    max(0, start_ms - HOUR_MS), end_ms)

    mark_closes: dict[int, float] = {}
    for index, row in enumerate(mark_raw):
        if len(row) < 8:
            raise ValueError(f"archive mark-price kline row {index} has fewer than 8 fields")
        ts = int(row[0])
        mark_closes[ts] = float(row[4])
    regular_closes: dict[int, float] = {}
    for index, row in enumerate(regular_raw):
        if len(row) < 8:
            raise ValueError(f"archive regular-price kline row {index} has fewer than 8 fields")
        regular_closes[int(row[0])] = float(row[4])

    output = []
    for index, row in enumerate(funding_raw):
        if len(row) < 3:
            raise ValueError(f"archive funding row {index} has fewer than 3 fields")
        ts = int(row[0])
        if not start_ms <= ts < end_ms:
            continue
        # Only use a fully closed candle ending at/before the funding timestamp.
        prior_open = (ts // HOUR_MS) * HOUR_MS - HOUR_MS
        mark_price = mark_closes.get(prior_open)
        if mark_price is not None and mark_price > 0:
            price_source = "binance_archive_prior_closed_mark_price_1h_proxy_NOT_SETTLEMENT_MARK"
        else:
            mark_price = regular_closes.get(prior_open)
            price_source = "binance_archive_prior_closed_regular_price_1h_proxy_NOT_SETTLEMENT_MARK"
        if mark_price is None or mark_price <= 0:
            raise ValueError(
                f"no prior closed mark or regular-price candle for {symbol} funding timestamp {ts}; "
                "refusing to fabricate settlement mark"
            )
        rate = float(row[-1])
        output.append({
            "funding_time": ts,
            "funding_rate": rate,
            "mark_price": mark_price,
            "symbol": symbol,
            "mark_price_source": price_source,
            "venue": "binance_usdm_archive",
        })
    output.sort(key=lambda r: r["funding_time"])
    if not output:
        raise ValueError(f"no archived funding rows for {symbol} in requested range")
    for left, right in zip(output, output[1:]):
        if left["funding_time"] == right["funding_time"]:
            raise ValueError(f"duplicate archived funding timestamp {left['funding_time']}")
    return output
