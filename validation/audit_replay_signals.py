#!/usr/bin/env python3
"""Audit replayed signals against source 1h candles before exit backtests."""
import argparse
import csv
import json
import math
from datetime import datetime
from pathlib import Path

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT", "XRPUSDT"]


def timestamp_ms(value):
    dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(f"Timestamp must include timezone: {value!r}")
    return int(dt.timestamp() * 1000)


def read_close_map(path):
    with open(path, newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or "timestamp" not in reader.fieldnames or "close" not in reader.fieldnames:
            raise ValueError(f"Missing timestamp/close columns: {path}")
        result = {}
        previous = None
        for line, row in enumerate(reader, start=2):
            ts = timestamp_ms(row["timestamp"])
            close = float(row["close"])
            if not math.isfinite(close) or close <= 0:
                raise ValueError(f"Invalid close at {path}:{line}")
            if previous is not None and ts <= previous:
                raise ValueError(f"Source candle timestamps are not strictly increasing at {path}:{line}")
            if ts in result:
                raise ValueError(f"Duplicate source timestamp {ts} in {path}")
            result[ts] = close
            previous = ts
    if not result:
        raise ValueError(f"Empty source candle file: {path}")
    return result


def audit(symbol, candle_path, signal_path, entry_tolerance_bps=0.1):
    closes = read_close_map(candle_path)
    rows = []
    with open(signal_path, newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        required = {"timestamp", "side", "entry", "atr", "stop", "target"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Missing signal columns in {signal_path}: {sorted(missing)}")
        previous = None
        for line, row in enumerate(reader, start=2):
            ts = timestamp_ms(row["timestamp"])
            if previous is not None and ts <= previous:
                raise ValueError(f"Signal timestamps not strictly increasing at {signal_path}:{line}")
            previous = ts
            side = row["side"].strip().upper()
            if side not in ("LONG", "SHORT"):
                raise ValueError(f"Invalid side at {signal_path}:{line}")
            try:
                entry, atr, stop, target = (float(row[k]) for k in ("entry", "atr", "stop", "target"))
            except (ValueError, TypeError) as exc:
                raise ValueError(f"Invalid numeric signal field at {signal_path}:{line}") from exc
            if not all(math.isfinite(v) and v > 0 for v in (entry, atr, stop, target)):
                raise ValueError(f"Signal values must be finite and positive at {signal_path}:{line}")
            if ts not in closes:
                rows.append({"line": line, "timestamp_ms": ts, "error": "SIGNAL_TIMESTAMP_NOT_IN_SOURCE_CANDLES"})
                continue
            close = closes[ts]
            tolerance = entry_tolerance_bps / 10000.0
            if abs(entry - close) / close > tolerance:
                rows.append({"line": line, "timestamp_ms": ts, "error": "ENTRY_NOT_MATCHED_TO_SIGNAL_CANDLE_CLOSE",
                             "entry": entry, "candle_close": close})
                continue
            if (side == "LONG" and not stop < entry < target) or (side == "SHORT" and not target < entry < stop):
                rows.append({"line": line, "timestamp_ms": ts, "error": "STOP_TARGET_DIRECTION_INVALID",
                             "side": side, "entry": entry, "stop": stop, "target": target})
                continue
            rows.append({"line": line, "timestamp_ms": ts, "error": None})
    errors = [row for row in rows if row["error"]]
    return {
        "symbol": symbol, "source_candle_count": len(closes), "signal_count": len(rows),
        "matched_signal_count": len(rows) - len(errors), "error_count": len(errors),
        "entry_tolerance_bps": entry_tolerance_bps, "errors_sample": errors[:20],
        # Zero signals can be legitimate for a disabled/no-entry model (e.g. ETH).
        # Keep data integrity separate from signal availability; the batch audit
        # will mark a zero-trade model as insufficient sample rather than failing
        # the entire multi-asset run.
        "status": "NO_SIGNALS" if not rows else ("PASS" if not errors else "SIGNAL_ERRORS"),
        "pass": bool(closes) and not errors,
        "notice": "This checks timestamp/price/stop/target consistency, not parity with the live strategy or profitability."
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data/historical")
    parser.add_argument("--signals-dir", default="data/replay")
    parser.add_argument("--symbols", nargs="+", default=SYMBOLS)
    parser.add_argument("--entry-tolerance-bps", type=float, default=0.1)
    parser.add_argument("--out", default="research-results/signal-audit.json")
    args = parser.parse_args()
    if not math.isfinite(args.entry_tolerance_bps) or args.entry_tolerance_bps < 0:
        parser.error("--entry-tolerance-bps must be finite and >= 0")
    reports = []
    for symbol in args.symbols:
        try:
            reports.append(audit(symbol, Path(args.data_dir) / f"{symbol}_1h.csv",
                                 Path(args.signals_dir) / f"{symbol}_signals.csv",
                                 args.entry_tolerance_bps))
        except (OSError, ValueError) as exc:
            reports.append({"symbol": symbol, "pass": False, "error": str(exc)})
    result = {"all_signals_pass": bool(reports) and all(item.get("pass", False) for item in reports),
              "reports": reports,
              "notice": "Signal input audit only; strategy parity still requires independent verification."}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    if not result["all_signals_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
