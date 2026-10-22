#!/usr/bin/env python3
"""Reproducible microbenchmark for replay indicator optimizations.

This isolates indicator work; it is not a full signal-replay benchmark and must
not be presented as evidence of end-to-end replay speedup. Use the same Python
version and --bars value when comparing revisions.
"""
import argparse
import json
import math
import random
import sys
import time
from pathlib import Path

# Running this file directly puts validation/ on sys.path, not the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from validation.replay_dashboard_signals import (
    atr, ema, ema_series, levels, rsi, rsi_series, vwap,
)


def make_data(count):
    rng = random.Random(20261019)
    close = []
    value = 100.0
    for i in range(count):
        value *= 1 + 0.00015 + rng.uniform(-0.004, 0.004)
        close.append(value)
    high = [v * (1 + 0.001 + (i % 5) * 0.0001) for i, v in enumerate(close)]
    low = [v * (1 - 0.001 - (i % 7) * 0.0001) for i, v in enumerate(close)]
    quote = [100_000 * (1 + (i % 11) * 0.03) for i in range(count)]
    return close, high, low, quote


def timed(fn):
    start = time.perf_counter()
    result = fn()
    return time.perf_counter() - start, result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", type=int, default=8760)
    args = parser.parse_args()
    if args.bars < 250:
        parser.error("--bars must be at least 250")

    close, high, low, quote = make_data(args.bars)
    windows = range(199, args.bars)

    old_ema_time, old_ema = timed(lambda: [
        (ema(close[:i + 1], 20), ema(close[:i + 1], 50), ema(close[:i + 1], 200))
        for i in windows
    ])
    def precompute_ema_series():
        ema20 = ema_series(close, 20)
        ema50 = ema_series(close, 50)
        ema200 = ema_series(close, 200)
        return list(zip(ema20[199:], ema50[199:], ema200[199:]))

    new_ema_time, new_ema = timed(precompute_ema_series)
    # Compare exact historical-prefix outputs before reporting timing.
    if len(old_ema) != len(new_ema) or any(
        any(not math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12) for a, b in zip(old, new))
        for old, new in zip(old_ema, new_ema)
    ):
        raise AssertionError("EMA series differs from repeated-prefix reference")

    old_rsi_time, old_rsi = timed(lambda: [rsi(close[:i + 1]) for i in windows])
    new_rsi_time, series_rsi = timed(lambda: rsi_series(close))
    new_rsi = series_rsi[199:]
    if len(old_rsi) != len(new_rsi) or any(
        (a is None) != (b is None) or
        (a is not None and not math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12))
        for a, b in zip(old_rsi, new_rsi)
    ):
        raise AssertionError("RSI series differs from repeated-prefix reference")

    full_window_time, full_window = timed(lambda: [
        (atr(high[:i + 1], low[:i + 1], close[:i + 1]),
         vwap(high[:i + 1], low[:i + 1], close[:i + 1], quote[:i + 1]),
         levels(high[:i + 1], low[:i + 1], close[:i + 1]))
        for i in windows
    ])
    bounded_time, bounded_window = timed(lambda: [
        (atr(high[max(0, i - 29):i + 1], low[max(0, i - 29):i + 1], close[max(0, i - 29):i + 1]),
         vwap(high[max(0, i - 47):i + 1], low[max(0, i - 47):i + 1], close[max(0, i - 47):i + 1], quote[max(0, i - 47):i + 1]),
         levels(high[max(0, i - 47):i + 1], low[max(0, i - 47):i + 1], close[max(0, i - 47):i + 1]))
        for i in windows
    ])
    # The rolling functions are intended to depend only on the most recent
    # window. Prove that bounded inputs preserve ATR, quote-volume VWAP, support
    # and resistance before reporting any speedup.
    if len(full_window) != len(bounded_window):
        raise AssertionError("bounded rolling indicator result length differs")
    for index, (full, bounded) in enumerate(zip(full_window, bounded_window)):
        for full_value, bounded_value in zip(full, bounded):
            if isinstance(full_value, tuple):
                if full_value != bounded_value:
                    raise AssertionError(f"bounded support/resistance parity failed at decision {index}")
            elif full_value is None or bounded_value is None:
                if full_value is not bounded_value:
                    raise AssertionError(f"bounded rolling indicator null parity failed at decision {index}")
            elif not math.isclose(full_value, bounded_value, rel_tol=1e-12, abs_tol=1e-12):
                raise AssertionError(f"bounded rolling indicator parity failed at decision {index}")

    report = {
        "benchmark": "indicator_microbenchmark_only",
        "bars": args.bars,
        "decisions": args.bars - 199,
        "ema_repeated_prefix_seconds": round(old_ema_time, 6),
        "ema_precomputed_series_seconds": round(new_ema_time, 6),
        "ema_speedup": round(old_ema_time / max(new_ema_time, 1e-12), 3),
        "rsi_repeated_prefix_seconds": round(old_rsi_time, 6),
        "rsi_precomputed_series_seconds": round(new_rsi_time, 6),
        "rsi_speedup": round(old_rsi_time / max(new_rsi_time, 1e-12), 3),
        "rolling_indicators_full_history_seconds": round(full_window_time, 6),
        "rolling_indicators_bounded_window_seconds": round(bounded_time, 6),
        "rolling_indicators_speedup": round(full_window_time / max(bounded_time, 1e-12), 3),
        "parity": "passed",
        "rolling_indicator_parity": "passed",
        "limitations": "Synthetic deterministic data; indicator-only timings, not end-to-end replay performance.",
    }
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
