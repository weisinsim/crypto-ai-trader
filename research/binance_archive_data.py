"""Download USD-M futures klines from Binance's public data archive (REST-API fallback)."""
from __future__ import annotations

import argparse
import calendar
import csv
import hashlib
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

INTERVAL_MS = {"1h": 3_600_000, "4h": 14_400_000}
FIELDS = ("t", "o", "h", "l", "c", "v", "q")
BASE = "https://data.binance.vision/data/futures/um"


def _validated_candle(raw: list, timestamp: int) -> dict:
    """Parse and validate OHLCV values before accepting archive/API data."""
    import math

    try:
        values = {
            "o": float(raw[1]), "h": float(raw[2]), "l": float(raw[3]),
            "c": float(raw[4]), "v": float(raw[5]), "q": float(raw[7]),
        }
    except (TypeError, ValueError, IndexError) as exc:
        raise ValueError(f"invalid OHLCV values at candle timestamp {timestamp}") from exc
    if not all(math.isfinite(value) for value in values.values()):
        raise ValueError(f"non-finite OHLCV values at candle timestamp {timestamp}")
    if min(values["o"], values["h"], values["l"], values["c"]) <= 0:
        raise ValueError(f"non-positive OHLC price at candle timestamp {timestamp}")
    if values["v"] < 0 or values["q"] < 0:
        raise ValueError(f"negative volume at candle timestamp {timestamp}")
    if values["h"] < max(values["o"], values["l"], values["c"]):
        raise ValueError(f"high price inconsistent with OHLC at candle timestamp {timestamp}")
    if values["l"] > min(values["o"], values["h"], values["c"]):
        raise ValueError(f"low price inconsistent with OHLC at candle timestamp {timestamp}")
    return {"t": timestamp, **values}


def _read_zip(payload: bytes, start_ms: int, end_ms: int, interval_ms: int) -> list[dict]:
    rows = []
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for name in archive.namelist():
            with archive.open(name) as handle:
                for raw in csv.reader(io.TextIOWrapper(handle, encoding="utf-8")):
                    if not raw or len(raw) < 8 or not raw[0].strip().lstrip("-").isdigit():
                        continue
                    ts = int(raw[0])
                    if start_ms <= ts < end_ms and ts + interval_ms <= end_ms:
                        rows.append(_validated_candle(raw, ts))
    return rows


def download_archive(symbol: str, interval: str, start_ms: int, end_ms: int) -> list[dict]:
    if interval not in INTERVAL_MS:
        raise ValueError(f"unsupported interval: {interval}")
    step = INTERVAL_MS[interval]
    if start_ms < 0 or end_ms <= start_ms or start_ms % step or end_ms % step:
        raise ValueError("date range must be positive and aligned to candle boundaries")
    symbol = symbol.upper()
    rows = []
    start_month = datetime.fromtimestamp(start_ms / 1000, timezone.utc).replace(day=1)
    end_month = datetime.fromtimestamp((end_ms - 1) / 1000, timezone.utc).replace(day=1)
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        month = start_month
        while month <= end_month:
            month_end = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
            name = f"{symbol}-{interval}-{month:%Y-%m}.zip"
            url = f"{BASE}/monthly/klines/{symbol}/{interval}/{name}"
            response = client.get(url)
            if response.status_code == 200:
                rows.extend(_read_zip(response.content, start_ms, end_ms, step))
            elif response.status_code == 404:
                # Monthly file may not exist yet for the current month.
                day = max(month, datetime.fromtimestamp(start_ms / 1000, timezone.utc))
                last_day = min(month_end, datetime.fromtimestamp((end_ms - 1) / 1000, timezone.utc) + timedelta(days=1))
                while day < last_day:
                    daily_name = f"{symbol}-{interval}-{day:%Y-%m-%d}.zip"
                    daily_url = f"{BASE}/daily/klines/{symbol}/{interval}/{daily_name}"
                    daily = client.get(daily_url)
                    if daily.status_code == 200:
                        rows.extend(_read_zip(daily.content, start_ms, end_ms, step))
                    elif daily.status_code != 404:
                        daily.raise_for_status()
                    day += timedelta(days=1)
            else:
                response.raise_for_status()
            month = month_end

    rows.sort(key=lambda row: row["t"])
    deduped = {row["t"]: row for row in rows}
    rows = list(deduped.values())
    expected = list(range(start_ms, end_ms, step))
    present = {row["t"] for row in rows}
    missing = [ts for ts in expected if ts not in present]
    # Binance Vision may lag for the current month. Fill only missing, fully
    # closed candles from the official USD-M Futures REST kline endpoint.
    # Keep archive rows authoritative wherever they exist.
    if missing:
        api_url = "https://fapi.binance.com/fapi/v1/klines"
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            for offset in range(0, len(missing), 1000):
                batch = missing[offset:offset + 1000]
                response = client.get(api_url, params={
                    "symbol": symbol, "interval": interval,
                    "startTime": batch[0], "endTime": batch[-1] + step - 1,
                    "limit": min(1500, len(batch) + 100),
                })
                response.raise_for_status()
                for raw in response.json():
                    ts = int(raw[0])
                    close_ts = int(raw[6])
                    if ts in batch and close_ts < int(datetime.now(timezone.utc).timestamp() * 1000):
                        deduped[ts] = _validated_candle(raw, ts)
        rows = [deduped[ts] for ts in sorted(deduped)]
    actual = [row["t"] for row in rows]
    if actual != expected:
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        raise ValueError(
            f"incomplete archive coverage for {symbol} {interval}: expected {len(expected)} bars, "
            f"got {len(actual)}; missing_first={missing[:5]}, extra_first={extra[:5]}"
        )
    return rows


def build_output_manifest(
    output_path: str | Path, *, symbol: str, interval: str,
    start_ms: int, end_ms: int, bars: int,
) -> dict:
    """Describe the normalized download artifact; provenance claims remain unverified."""
    path = Path(output_path)
    digest = hashlib.sha256()
    size_bytes = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size_bytes += len(chunk)
    return {
        "manifest_version": 1,
        "status": "STRUCTURAL_COVERAGE_COMPLETE",
        "symbol": symbol.upper(),
        "interval": interval,
        "requested_range_ms": {"start_inclusive": start_ms, "end_exclusive": end_ms},
        "bars": bars,
        "source_claims": [
            "Binance Vision USD-M Futures public archive",
            "Binance USD-M Futures REST klines fallback when archive coverage is missing",
        ],
        "file": {"path": str(path), "size_bytes": size_bytes, "sha256": digest.hexdigest()},
        "source_authenticity_verified": False,
        "note": "Exact candle-grid coverage was checked by the downloader; this manifest does not retain per-request HTTP evidence or independently authenticate the upstream source.",
    }


def utc_ms(date_text: str) -> int:
    return int(datetime.strptime(date_text, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--interval", choices=sorted(INTERVAL_MS), required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True, help="exclusive UTC date YYYY-MM-DD")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    rows = download_archive(args.symbol, args.interval, utc_ms(args.start), utc_ms(args.end))
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    manifest = build_output_manifest(
        destination, symbol=args.symbol, interval=args.interval,
        start_ms=utc_ms(args.start), end_ms=utc_ms(args.end), bars=len(rows),
    )
    manifest_path = destination.with_suffix(destination.suffix + ".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"status=COMPLETE source=binance_vision_with_rest_fallback symbol={args.symbol.upper()} "
          f"interval={args.interval} bars={len(rows)} output={destination} manifest={manifest_path}")


if __name__ == "__main__":
    main()
