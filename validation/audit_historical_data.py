#!/usr/bin/env python3
"""Audit historical candle CSV completeness before replay/backtesting."""
import argparse
import csv
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path

INTERVAL_MS = {"15m": 900_000, "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "1d": 86_400_000}


def parse_ts(v):
    dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(f"Timestamp must include timezone: {v!r}")
    return int(dt.timestamp() * 1000)


def _parse_candle_timestamp(row):
    # Archive-normalized research CSVs use integer epoch milliseconds in "t";
    # legacy replay CSVs use timezone-aware ISO timestamps in "timestamp".
    value = row.get("timestamp")
    if value not in (None, ""):
        return parse_ts(value)
    value = row.get("t")
    if value in (None, ""):
        raise ValueError("missing timestamp/t field")
    if isinstance(value, bool):
        raise ValueError("invalid epoch-millisecond timestamp")
    text = str(value).strip()
    if not text.isdigit():
        raise ValueError(f"invalid epoch-millisecond timestamp: {text!r}")
    return int(text)


def _valid_timestamp(row):
    try:
        _parse_candle_timestamp(row)
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def file_provenance(path):
    """Fingerprint exact source bytes for reproducible candle audits."""
    digest = hashlib.sha256()
    size_bytes = 0
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size_bytes += len(chunk)
    return {"sha256": digest.hexdigest(), "size_bytes": size_bytes}


def audit(path, interval):
    if interval not in INTERVAL_MS:
        raise ValueError(f"Unsupported interval: {interval}")
    provenance = file_provenance(path)
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    try:
        ts = [_parse_candle_timestamp(r) for r in rows]
        timestamp_errors = 0
    except (ValueError, TypeError, KeyError):
        timestamp_errors = sum(1 for r in rows if not _valid_timestamp(r))
        ts = []
    if timestamp_errors:
        return {"file": str(path), **provenance, "rows": len(rows), "timestamp_errors": timestamp_errors,
                "pass_basic_integrity": False, "error": "INVALID_OR_TIMEZONE_NAIVE_TIMESTAMP"}
    if not rows:
        return {"file": str(path), **provenance, "rows": 0, "timestamp_errors": 0,
                "pass_basic_integrity": False, "error": "EMPTY_FILE"}
    duplicates = len(ts) - len(set(ts))
    non_increasing = sum(1 for a, b in zip(ts, ts[1:]) if b <= a)
    gaps = []
    cadence_anomalies = []
    step = INTERVAL_MS[interval]
    for a, b in zip(ts, ts[1:]):
        delta = b - a
        if delta > step:
            gaps.append({"after": a, "before": b, "missing_approx": max(0, delta//step-1)})
        if delta != step:
            cadence_anomalies.append({"after": a, "before": b, "delta_ms": delta})
    invalid_ohlc = 0
    invalid_volume = 0
    volume_fields = ("volume", "quote_volume", "v", "q")
    # Volume is required for volume-aware strategies; silently accepting a file
    # with no volume column would make a price-only dataset look research-ready.
    missing_volume_column = not any(field in (rows[0] if rows else {}) for field in volume_fields)
    for r in rows:
        try:
            aliases = (("open", "o"), ("high", "h"), ("low", "l"), ("close", "c"))
            o, h, low, close = (
                float(next(r[key] for key in names if key in r and r[key] not in (None, "")))
                for names in aliases
            )
            valid = all(math.isfinite(v) and v > 0 for v in (o, h, low, close))
            valid = valid and low <= min(o, close) and h >= max(o, close) and low <= h
        except (ValueError, TypeError, KeyError):
            valid = False
        if not valid:
            invalid_ohlc += 1
        present_volume_fields = [field for field in volume_fields if field in r and r[field] not in (None, "")]
        try:
            volumes = [float(r[field]) for field in present_volume_fields]
            volume_valid = all(math.isfinite(value) and value >= 0 for value in volumes)
        except (ValueError, TypeError):
            volume_valid = False
        if not volume_valid:
            invalid_volume += 1
    return {"file": str(path), **provenance, "rows": len(rows), "first_close_ms": ts[0] if ts else None,
            "last_close_ms": ts[-1] if ts else None, "duplicates": duplicates,
            "non_increasing_pairs": non_increasing, "gap_count": len(gaps),
            "cadence_anomaly_count": len(cadence_anomalies), "cadence_anomalies_sample": cadence_anomalies[:10],
            "missing_candles_approx": sum(g["missing_approx"] for g in gaps),
            "invalid_ohlc_rows": invalid_ohlc, "invalid_volume_rows": invalid_volume,
            "volume_fields_checked": list(volume_fields),
            "missing_volume_column": missing_volume_column,
            "gaps_sample": gaps[:10], "timestamp_errors": timestamp_errors,
            "pass_basic_integrity": bool(rows) and duplicates == 0 and non_increasing == 0
                and invalid_ohlc == 0 and invalid_volume == 0 and not missing_volume_column
                and not cadence_anomalies}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-dir", default="data/historical")
    p.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT", "XRPUSDT"])
    args = p.parse_args()
    root = Path(args.data_dir)
    report = []
    for symbol in args.symbols:
        for interval in ("1h", "4h"):
            path = root / f"{symbol}_{interval}.csv"
            if not path.exists():
                report.append({"file": str(path), "error": "MISSING_FILE", "pass_basic_integrity": False})
            else:
                report.append(audit(path, interval))
    print(json.dumps({"notice": "A basic integrity pass does not establish gap-free history or strategy profitability.", "files": report,
                      "all_basic_integrity_pass": all(x.get("pass_basic_integrity", False) for x in report)}, indent=2))


if __name__ == "__main__":
    main()
