"""Deterministic, research-only replay adapter for BTC/SOL/AVAX tactical scoring.

Inputs must be normalized OHLCV rows. This module deliberately performs no I/O,
reads no wall clock, mutates no application cache, and places no orders.
Indicator semantics mirror the dashboard helpers in app.py; keep parity tests
when those helpers change.
"""
from __future__ import annotations

import math

from research.historical_replay_inputs import normalize_closed_candle_window
from research.tactical_scorer import score_tactical_setup


def _ema(values, period):
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    value = sum(values[:period]) / period
    for item in values[period:]:
        value = item * k + value * (1 - k)
    return value


def _rsi(values, period=14):
    if len(values) < period + 1:
        return None
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(change, 0) for change in changes]
    losses = [max(-change, 0) for change in changes]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    return 100 - (100 / (1 + avg_gain / avg_loss))


def _atr(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return None
    ranges = [
        max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]))
        for i in range(1, len(closes))
    ]
    return sum(ranges[-period:]) / period


def _adx(highs, lows, closes, period=14):
    if len(closes) < period * 2 + 1:
        return None
    trs, plus, minus = [], [], []
    for i in range(1, len(closes)):
        up, down = highs[i] - highs[i - 1], lows[i - 1] - lows[i]
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]),
                       abs(lows[i] - closes[i - 1])))
        plus.append(up if up > down and up > 0 else 0)
        minus.append(down if down > up and down > 0 else 0)

    def average(items):
        return sum(items[-period:]) / period if len(items) >= period else None

    tr, pos, neg = average(trs), average(plus), average(minus)
    if not tr or tr == 0:
        return None
    pdi, mdi = 100 * pos / tr, 100 * neg / tr
    dx = 100 * abs(pdi - mdi) / max(pdi + mdi, 1e-9)
    dx_values = []
    for end in range(period, len(trs)):
        tr_end, pos_end, neg_end = average(trs[:end + 1]), average(plus[:end + 1]), average(minus[:end + 1])
        if tr_end and tr_end > 0:
            p, m = 100 * pos_end / tr_end, 100 * neg_end / tr_end
            dx_values.append(100 * abs(p - m) / max(p + m, 1e-9))
    return sum(dx_values[-period:]) / min(period, len(dx_values)) if dx_values else dx


def _vwap(highs, lows, closes, quote_volumes, period=48):
    n = min(period, len(closes))
    if n == 0:
        return None
    numerator = sum(((h + l + c) / 3) * q for h, l, c, q in
                    zip(highs[-n:], lows[-n:], closes[-n:], quote_volumes[-n:]))
    denominator = sum(quote_volumes[-n:])
    return numerator / denominator if denominator else None


def _support_resistance(highs, lows, closes, lookback=48):
    n = min(lookback, len(closes))
    if n < 5:
        return None, None
    return min(lows[-n:-1]), max(highs[-n:-1])


def replay_tactical_setup(*, symbol, candles_1h, candles_4h, now_ms,
                          model_status="RESEARCH"):
    """Replay the latest fully closed 1H candle using only frozen 1H/4H windows."""
    if symbol not in {"BTCUSDT", "SOLUSDT", "AVAXUSDT"}:
        raise ValueError("replay_tactical_setup supports BTCUSDT, SOLUSDT, AVAXUSDT only")
    one_hour = normalize_closed_candle_window(
        candles_1h, timeframe="1h", now_ms=now_ms, minimum_rows=200
    )
    four_hour = normalize_closed_candle_window(
        candles_4h, timeframe="4h", now_ms=now_ms, minimum_rows=50
    )
    c1 = [row["c"] for row in one_hour]
    h1 = [row["h"] for row in one_hour]
    l1 = [row["l"] for row in one_hour]
    q1 = [row["q"] for row in one_hour]
    c4 = [row["c"] for row in four_hour]
    price = c1[-1]
    ema20_1h = _ema(c1, 20)
    ema20_4h, ema50_4h = _ema(c4, 20), _ema(c4, 50)
    rsi_1h, atr_1h = _rsi(c1), _atr(h1, l1, c1)
    adx_1h = _adx(h1, l1, c1)
    volume_ratio = q1[-1] / (sum(q1[-21:-1]) / 20) if len(q1) >= 21 and sum(q1[-21:-1]) > 0 else None
    support, resistance = _support_resistance(h1, l1, c1)
    scored = score_tactical_setup(
        symbol=symbol, model_status=model_status, price=price,
        ema20_1h=ema20_1h, ema20_4h=ema20_4h, ema50_4h=ema50_4h,
        rsi_1h=rsi_1h, atr_1h=atr_1h, adx_1h=adx_1h,
        volume_ratio=volume_ratio, previous_close=c1[-2],
        support=support, resistance=resistance,
    )
    return {
        "symbol": symbol,
        "model_status": model_status,
        "model_version": "shared-tactical-scorer-v1",
        "research_only": True,
        "auto_trade_authorized": False,
        "timeframe": "1h",
        "signal_candle_open_ms": one_hour[-1]["t"],
        "signal_candle_close_ms": one_hour[-1]["t"] + 3_600_000,
        "latest_4h_candle_open_ms": four_hour[-1]["t"],
        "closed_1h_candles": len(one_hour),
        "closed_4h_candles": len(four_hour),
        "indicators": {
            "price": price, "ema20_1h": ema20_1h,
            "ema20_4h": ema20_4h, "ema50_4h": ema50_4h,
            "rsi_1h": rsi_1h, "atr_1h": atr_1h, "adx_1h": adx_1h,
            "volume_ratio": volume_ratio,
            "vwap_1h": _vwap(h1, l1, c1, q1, 48),
            "support": support, "resistance": resistance,
        },
        **scored,
    }
