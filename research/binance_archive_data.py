"""Download USD-M futures klines from Binance's public data archive (REST-API fallback)."""
from __future__ import annotations

import argparse
import calendar
import csv
import io
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

INTERVAL_MS = {"1h": 3_600_000, "4h": 14_400_000}
FIELDS = ("t", "o", "h", "l", "c", "v", "q")
BASE = "https://data.binance.vision/data/futures/um"


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
                        rows.append({
                            "t": ts, "o": float(raw[1]), "h": float(raw[2]),
                            "l": float(raw[3]), "c": float(raw[4]), "v": float(raw[5]),
                            "q": float(raw[7]),
                        })
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
    actual = [row["t"] for row in rows]
    if actual != expected:
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        raise ValueError(
            f"incomplete archive coverage for {symbol} {interval}: expected {len(expected)} bars, "
            f"got {len(actual)}; missing_first={missing[:5]}, extra_first={extra[:5]}"
        )
    return rows


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
    print(f"status=COMPLETE source=binance_vision symbol={args.symbol.upper()} "
          f"interval={args.interval} bars={len(rows)} output={destination}")


if __name__ == "__main__":
    main()
