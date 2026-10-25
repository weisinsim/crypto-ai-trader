"""Validated historical candle windows for deterministic research replay.

This module prepares frozen, closed-candle inputs only. It intentionally does not
calculate model scores, fetch market data, mutate app runtime caches, or place orders.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from validation.candle_closure import filter_closed_candles, validate_candle_series

_INTERVAL_MS = {"1h": 3_600_000, "4h": 14_400_000}
_REQUIRED = ("t", "o", "h", "l", "c", "v", "q")


def normalize_closed_candle_window(rows, *, timeframe, now_ms, minimum_rows=1):
    """Validate and freeze chronological, finite, geometrically valid closed candles.

    Rows use the repository's normalized format: millisecond open timestamp and
    numeric OHLCV/quote-volume fields. `now_ms` is injected by the caller so replay
    tests are deterministic and cannot silently consult wall-clock time.
    """
    if timeframe not in _INTERVAL_MS:
        raise ValueError("timeframe must be '1h' or '4h'")
    if isinstance(minimum_rows, bool) or not isinstance(minimum_rows, int) or minimum_rows < 1:
        raise ValueError("minimum_rows must be a positive integer")
    if not isinstance(rows, (list, tuple)):
        raise ValueError("rows must be a list or tuple")

    validated = validate_candle_series(rows, limit=max(len(rows), 1))
    frozen = []
    for index, row in enumerate(validated):
        missing = [key for key in _REQUIRED if key not in row]
        if missing:
            raise ValueError(f"candle row {index} missing fields: {', '.join(missing)}")
        candle = {"t": row["t"]}
        for key in _REQUIRED[1:]:
            value = row[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"candle row {index} field {key} must be finite numeric")
            candle[key] = float(value)
        if min(candle["o"], candle["h"], candle["l"], candle["c"]) <= 0:
            raise ValueError(f"candle row {index} OHLC prices must be positive")
        if candle["v"] < 0 or candle["q"] < 0:
            raise ValueError(f"candle row {index} volumes must be nonnegative")
        if candle["h"] < max(candle["o"], candle["c"], candle["l"]):
            raise ValueError(f"candle row {index} high is inconsistent with OHLC")
        if candle["l"] > min(candle["o"], candle["c"], candle["h"]):
            raise ValueError(f"candle row {index} low is inconsistent with OHLC")
        frozen.append(candle)

    closed = filter_closed_candles(frozen, timeframe, now_ms)
    if len(closed) < minimum_rows:
        raise ValueError(
            f"insufficient closed candles for {timeframe}: "
            f"need {minimum_rows}, got {len(closed)}"
        )
    # Return detached dicts so later caller mutation of the original rows cannot
    # alter the exact input snapshot that is passed to a replay scorer.
    return tuple(dict(row) for row in closed)
