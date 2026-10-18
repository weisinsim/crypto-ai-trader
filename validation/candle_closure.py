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
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or "t" not in row:
            raise ValueError(f"candle row {index} must be a mapping with timestamp 't'")
        timestamp = _timestamp_ms(row["t"], field=f"candle row {index} timestamp")
        if timestamp + duration_ms <= now_ms:
            closed.append(row)
    return closed
