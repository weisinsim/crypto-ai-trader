"""Time-based closed-candle filtering shared by live analysis and research tests."""
from __future__ import annotations

import math
from numbers import Integral


def _timestamp_ms(value, *, field: str) -> int:
    """Accept integer milliseconds or digit-only integer strings, never truncate."""
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer millisecond timestamp")
    if isinstance(value, Integral):
        result = int(value)
    elif isinstance(value, str) and value.strip().isdigit():
        result = int(value.strip())
    elif isinstance(value, float) and math.isfinite(value) and value.is_integer():
        result = int(value)
    else:
        raise ValueError(f"{field} must be an integer millisecond timestamp")
    if result < 0:
        raise ValueError(f"{field} must be nonnegative")
    return result


def filter_closed_candles(rows, interval, now_ms):
    """Filter kline rows by open timestamp (t) and interval duration in milliseconds."""
    duration_ms = {"1h": 3_600_000, "4h": 14_400_000}.get(interval)
    if duration_ms is None:
        raise ValueError(f"Unsupported interval: {interval}")
    now_ms = _timestamp_ms(now_ms, field="now_ms")
    closed = []
    previous_timestamp = None
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or "t" not in row:
            raise ValueError(f"candle row {index} must be a mapping with timestamp 't'")
        timestamp = _timestamp_ms(row["t"], field=f"candle row {index} timestamp")
        if previous_timestamp is not None and timestamp <= previous_timestamp:
            raise ValueError(
                f"candle timestamps must be strictly increasing: row {index} timestamp {timestamp} "
                f"is not after {previous_timestamp}"
            )
        previous_timestamp = timestamp
        if timestamp + duration_ms <= now_ms:
            closed.append(row)
    return closed


def upsert_candle(series, candle, *, limit=210):
    """Append a newer candle, replace the current candle, and ignore stale events.

    Returns False for an out-of-order candle without mutating the series.
    """
    if not isinstance(candle, dict) or "t" not in candle:
        raise ValueError("candle must be a mapping with timestamp 't'")
    timestamp = _timestamp_ms(candle["t"], field="candle timestamp")
    if series:
        previous = _timestamp_ms(series[-1].get("t"), field="last candle timestamp")
        if timestamp < previous:
            return False
        if timestamp == previous:
            series[-1] = candle
            return True
    series.append(candle)
    del series[:-limit]
    return True


def validate_candle_series(rows, *, limit=210):
    """Validate a REST snapshot is strictly chronological with no duplicate opens."""
    if not isinstance(rows, (list, tuple)):
        raise ValueError("candle series must be a list or tuple")
    result = []
    previous = None
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or "t" not in row:
            raise ValueError(f"candle row {index} must be a mapping with timestamp 't'")
        timestamp = _timestamp_ms(row["t"], field=f"candle row {index} timestamp")
        if previous is not None and timestamp <= previous:
            raise ValueError("candle series timestamps must be strictly increasing")
        result.append(row)
        previous = timestamp
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit must be a positive integer")
    return result[-limit:]


def normalize_timestamp_ms(value) -> int:
    """Public strict timestamp parser for exchange adapters; never truncate fractions."""
    return _timestamp_ms(value, field="candle timestamp")
