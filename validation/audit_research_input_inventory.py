#!/usr/bin/env python3
"""Inventory candle and funding CSV inputs for a fixed research window.

This is a fail-closed inventory/provenance check, not proof that funding
settlements are complete or that the data came from an authentic exchange.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT", "XRPUSDT")
INTERVALS = {"1h": 3_600_000, "4h": 14_400_000}


def utc_ms(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"Timestamp must include timezone: {value!r}")
    return int(parsed.timestamp() * 1000)


def fingerprint(path: Path) -> dict:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return {"sha256": digest.hexdigest(), "size_bytes": size}


def inspect_csv(path: Path, kind: str, start_ms: int, end_ms: int,
                interval: str | None = None) -> dict:
    result = {"path": str(path), "kind": kind, "exists": path.is_file()}
    if not path.is_file():
        return {**result, "status": "MISSING", "pass": False}
    try:
        with path.open(newline="", encoding="utf-8-sig") as stream:
            reader = csv.DictReader(stream)
            required = {"timestamp", "funding_rate"} if kind == "funding" else {"timestamp", "open", "high", "low", "close"}
            if not reader.fieldnames or not required.issubset(reader.fieldnames):
                raise ValueError(f"Required columns missing: {sorted(required)}")
            timestamps = []
            rates = []
            for line, row in enumerate(reader, start=2):
                ts = utc_ms((row.get("timestamp") or "").strip())
                timestamps.append(ts)
                if kind == "funding":
                    rate = float(row["funding_rate"])
                    if not math.isfinite(rate):
                        raise ValueError(f"Non-finite funding rate at line {line}")
                    rates.append(rate)
                else:
                    values = [float(row[field]) for field in ("open", "high", "low", "close")]
                    if not all(math.isfinite(value) for value in values):
                        raise ValueError(f"Non-finite OHLC at line {line}")
                    if min(values) <= 0 or values[1] < max(values[0], values[3]) or values[2] > min(values[0], values[3]) or values[1] < values[2]:
                        raise ValueError(f"Invalid OHLC relationship at line {line}")
    except (OSError, ValueError, TypeError) as exc:
        return {**result, **fingerprint(path), "status": "INVALID", "pass": False, "error": str(exc)}

    duplicates = len(timestamps) != len(set(timestamps))
    ordered = all(a < b for a, b in zip(timestamps, timestamps[1:]))
    in_window = [ts for ts in timestamps if start_ms <= ts < end_ms]
    outside = len(timestamps) - len(in_window)
    base = {
        **result, **fingerprint(path), "rows": len(timestamps),
        "first_timestamp_ms": timestamps[0] if timestamps else None,
        "last_timestamp_ms": timestamps[-1] if timestamps else None,
        "rows_in_requested_window": len(in_window),
        "rows_outside_requested_window": outside,
        "duplicate_timestamps": duplicates, "strictly_increasing": ordered,
    }
    if not timestamps:
        return {**base, "status": "EMPTY", "pass": False}
    if duplicates or not ordered or outside:
        return {**base, "status": "TIMESTAMP_INTEGRITY_FAILED", "pass": False}
    if kind == "candle":
        step = INTERVALS[interval]
        # Candle timestamps are close times: k * step - 1 ms. Anchor to the UTC epoch,
        # not to the requested start, which may fall mid-candle.
        first_close = ((start_ms + 1 + step - 1) // step) * step - 1
        expected = list(range(first_close, end_ms, step))
        missing = sorted(set(expected) - set(in_window))
        unexpected = sorted(set(in_window) - set(expected))
        return {
            **base, "expected_rows": len(expected),
            "missing_expected_rows": len(missing),
            "unexpected_rows": len(unexpected),
            "missing_examples_ms": missing[:10],
            "unexpected_examples_ms": unexpected[:10],
            "status": "PASS_CANDLE_WINDOW" if expected and not missing and not unexpected else "CANDLE_WINDOW_INCOMPLETE",
            "pass": bool(expected) and not missing and not unexpected,
        }
    return {**base, "status": "FUNDING_INVENTORIED_NOT_RECONCILED",
            "pass": bool(in_window), "notice": "Funding row inventory does not prove settlement-event completeness."}


def build_inventory(candle_dir: Path, funding_dir: Path, start: str, end: str,
                    symbols: list[str] | None = None) -> dict:
    start_ms, end_ms = utc_ms(start), utc_ms(end)
    if end_ms <= start_ms:
        raise ValueError("end must be later than start")
    assets = []
    for symbol in symbols or list(SYMBOLS):
        reports = []
        for interval in INTERVALS:
            reports.append(inspect_csv(candle_dir / f"{symbol}_{interval}.csv",
                                       "candle", start_ms, end_ms, interval))
        reports.append(inspect_csv(funding_dir / f"{symbol}_funding.csv",
                                   "funding", start_ms, end_ms))
        assets.append({"symbol": symbol, "inputs": reports,
                       "all_inputs_inventory_pass": all(item["pass"] for item in reports)})
    all_pass = bool(assets) and all(asset["all_inputs_inventory_pass"] for asset in assets)
    return {
        "status": "RESEARCH_ONLY",
        "start_utc_inclusive": start,
        "end_utc_exclusive": end,
        "symbols": [asset["symbol"] for asset in assets],
        "all_inputs_inventory_pass": all_pass,
        "funding_settlement_reconciliation_complete": False,
        "profitability_claim_eligible": False,
        "strategy_approval": False,
        "live_trading_enabled": False,
        "assets": assets,
        "interpretation": "Candle window coverage and source fingerprints are inventory checks. Funding presence is not settlement reconciliation or proof of complete event coverage. A pass never authorizes strategy promotion or live trading.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candle-dir", default="data/historical")
    parser.add_argument("--funding-dir", default="data/funding")
    parser.add_argument("--start", required=True, help="UTC inclusive ISO-8601 timestamp")
    parser.add_argument("--end", required=True, help="UTC exclusive ISO-8601 timestamp")
    parser.add_argument("--symbols", nargs="+", default=list(SYMBOLS))
    parser.add_argument("--out", default="research-results/input-inventory.json")
    args = parser.parse_args()
    result = build_inventory(Path(args.candle_dir), Path(args.funding_dir),
                             args.start, args.end, args.symbols)
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["all_inputs_inventory_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
