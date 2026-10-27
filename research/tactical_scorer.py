"""Pure tactical scorer extracted for deterministic research replay.

This function covers BTC/SOL/AVAX only. XRP remains on its frozen dedicated model;
ETH remains suppressed as NO-PRODUCTION. It consumes precomputed indicators and
has no network, clock, cache, persistence, or order-execution dependencies.
"""
from __future__ import annotations

import math


def score_tactical_setup(*, symbol, model_status, price, ema20_1h, ema20_4h,
                         ema50_4h, rsi_1h, atr_1h, adx_1h, volume_ratio,
                         previous_close, support, resistance):
    """Return the legacy tactical decision fields without side effects."""
    if symbol not in {"BTCUSDT", "SOLUSDT", "AVAXUSDT"}:
        raise ValueError("score_tactical_setup supports BTCUSDT, SOLUSDT, AVAXUSDT only")
    values = {
        "price": price, "ema20_1h": ema20_1h, "ema20_4h": ema20_4h,
        "ema50_4h": ema50_4h, "rsi_1h": rsi_1h, "atr_1h": atr_1h,
        "adx_1h": adx_1h, "volume_ratio": volume_ratio,
        "previous_close": previous_close, "support": support, "resistance": resistance,
    }
    for name, value in values.items():
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
            raise ValueError(f"{name} must be finite numeric or None")
    if price is None or price <= 0 or ema20_1h is None or ema20_4h is None or ema50_4h is None or rsi_1h is None:
        raise ValueError("price, EMA values, and RSI are required")

    if symbol in {"SOLUSDT", "AVAXUSDT"}:
        rsi_long, rsi_short = (40, 52), (48, 60)
        sl_mult, tp_mult = (1.0, 2.0) if symbol == "SOLUSDT" else (1.2, 1.2)
    else:
        rsi_long, rsi_short = (45, 55), (45, 55)
        sl_mult, tp_mult = 1.2, 1.5

    bull = ema20_4h > ema50_4h and price > ema20_4h
    bear = ema20_4h < ema50_4h and price < ema20_4h
    long_ok = bull and rsi_long[0] <= rsi_1h <= rsi_long[1] and price > ema20_1h and previous_close is not None and price > previous_close
    short_ok = bear and rsi_short[0] <= rsi_1h <= rsi_short[1] and price < ema20_1h and previous_close is not None and price < previous_close
    signal, direction = "NO-TRADE", 0
    if long_ok:
        signal, direction = "LONG", 1
    elif short_ok:
        signal, direction = "SHORT", -1
    elif bull and rsi_1h <= rsi_long[1]:
        signal, direction = "WATCH-LONG", 1
    elif bear and rsi_1h >= rsi_short[0]:
        signal, direction = "WATCH-SHORT", -1

    entry = stop = target1 = target2 = None
    entry_location = "—"
    if direction:
        entry = price
        risk_atr = atr_1h or price * 0.005
        stop = price - direction * sl_mult * risk_atr
        target1 = price + direction * tp_mult * risk_atr
        target2 = price + direction * (tp_mult * 1.5) * risk_atr
        dist_support = max(0, price - support) if support else None
        dist_resistance = max(0, resistance - price) if resistance else None
        entry_location = "OPTIMAL" if (
            (direction == 1 and dist_support is not None and atr_1h and dist_support / atr_1h <= 2.5)
            or (direction == -1 and dist_resistance is not None and atr_1h and dist_resistance / atr_1h <= 2.5)
        ) else "TACTICAL"

    trend_points = 30 if (direction == 1 and bull) or (direction == -1 and bear) else 0
    rsi_points = 20 if (
        (direction == 1 and rsi_long[0] <= rsi_1h <= rsi_long[1])
        or (direction == -1 and rsi_short[0] <= rsi_1h <= rsi_short[1])
    ) else 0
    adx_points = 15 if adx_1h and adx_1h >= 20 else 8 if adx_1h and adx_1h >= 15 else 0
    volume_points = 15 if volume_ratio and volume_ratio >= 1.1 else 8 if volume_ratio and volume_ratio >= 0.8 else 0
    location_points = 10 if entry_location == "OPTIMAL" else 5 if direction else 0
    score = min(100, int(trend_points + rsi_points + adx_points + volume_points + location_points))
    score_label = "A" if score >= 75 else "B" if score >= 65 else "C" if score >= 50 else "NO-TRADE"
    score_breakdown = {
        "trend": trend_points, "rsi": rsi_points, "adx": adx_points,
        "volume": volume_points, "entry_location": location_points,
    }
    if model_status == "RESEARCH" and signal in ("LONG", "SHORT") and score < 75:
        signal = "WATCH-LONG" if direction == 1 else "WATCH-SHORT"
    return {
        "signal": signal, "direction": direction, "entry": entry, "sl": stop,
        "tp1": target1, "tp2": target2, "entry_location": entry_location,
        "score": score, "score_label": score_label, "score_breakdown": score_breakdown,
    }
