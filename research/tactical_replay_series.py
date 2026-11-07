"""Chronological, research-only orchestration for tactical candle replay.

Each decision is evaluated at its own 1H close. Full supplied arrays may contain
later rows, but the adapter's injected replay clock excludes any candle that had
not closed at that decision time. This emits signal observations, not trades.
"""
from __future__ import annotations

from research.tactical_replay import replay_tactical_setup

_HOUR_MS = 3_600_000


def replay_tactical_series(*, symbol, candles_1h, candles_4h,
                           model_status="RESEARCH", start_index=199):
    """Replay all eligible 1H closes in order, preventing future-candle leakage.

    start_index is zero-based and defaults to the earliest index that can meet
    the tactical adapter's 200 closed 1H candle minimum. Results contain only
    signal observations; there is no fill, stop/target simulation, or PnL.
    """
    if isinstance(start_index, bool) or not isinstance(start_index, int) or start_index < 0:
        raise ValueError("start_index must be a nonnegative integer")
    if not isinstance(candles_1h, (list, tuple)) or not isinstance(candles_4h, (list, tuple)):
        raise ValueError("candle series must be lists or tuples")
    observations = []
    for index in range(start_index, len(candles_1h)):
        row = candles_1h[index]
        if not isinstance(row, dict) or isinstance(row.get("t"), bool) or not isinstance(row.get("t"), int):
            raise ValueError(f"1h candle at index {index} has invalid timestamp")
        decision_time_ms = row["t"] + _HOUR_MS
        observation = replay_tactical_setup(
            symbol=symbol,
            candles_1h=candles_1h[:index + 1],
            candles_4h=candles_4h,
            now_ms=decision_time_ms,
            model_status=model_status,
        )
        observations.append(observation)
    return tuple(observations)
