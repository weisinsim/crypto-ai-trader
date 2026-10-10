"""Time-based closed-candle filtering shared by live analysis and research tests."""

def filter_closed_candles(rows, interval, now_ms):
    """Filter kline rows by open timestamp (t) and interval duration in milliseconds."""
    duration_ms = {"1h": 3_600_000, "4h": 14_400_000}.get(interval)
    if duration_ms is None:
        raise ValueError(f"Unsupported interval: {interval}")
    now_ms = int(now_ms)
    return [row for row in rows if int(row["t"]) + duration_ms <= now_ms]
