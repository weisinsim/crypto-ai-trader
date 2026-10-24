"""V12 Adaptive Institutional Hybrid decision engine.

Research/live-signal scoring only. It does not submit orders or assert profitability.
All features must be computed from closed candles and timestamp-aligned market data.
"""
from __future__ import annotations

from math import isfinite
from typing import Any, Mapping

MODEL_VERSION = "V12.0-Adaptive-Institutional-Hybrid"

# Conservative initial weights. They are explicit research defaults, not optimized
# parameters; the validation pipeline must select/promote models using out-of-sample data.
WEIGHTS = {
    "momentum_funding": 0.30,
    "trend_breakout": 0.30,
    "confluence": 0.25,
    "asset_adaptive": 0.15,
}

ASSET_PROFILES = {
    "BTCUSDT": {"family": "trend_breakout_retest", "min_score": 70, "max_atr_pct": 3.5},
    "ETHUSDT": {"family": "conditional_trend", "min_score": 72, "max_atr_pct": 4.0},
    "SOLUSDT": {"family": "momentum_pullback_breakout", "min_score": 72, "max_atr_pct": 7.0},
    "AVAXUSDT": {"family": "breakout_retest", "min_score": 74, "max_atr_pct": 8.0},
    "XRPUSDT": {"family": "frozen_external_model", "min_score": 75, "max_atr_pct": 5.0},
}


def _num(features: Mapping[str, Any], key: str) -> float | None:
    value = features.get(key)
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if isfinite(value) else None


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def evaluate_hybrid(symbol: str, features: Mapping[str, Any]) -> dict:
    """Return an auditable directional candidate from normalized feature inputs.

    Expected optional inputs:
      price, ema20_1h, ema50_1h, ema200_1h, ema20_4h, ema50_4h,
      ema200_4h, rsi_1h, atr_1h, atr_pct, adx_1h, vol_ratio, vwap_1h,
      support, resistance, funding_rate, oi_change_pct, momentum_pct,
      breakout_up, breakout_down, retest_long, retest_short, cvd_direction,
      market_regime, data_fresh, candle_closed, spread_bps.
    Missing critical data fails closed to NO-TRADE. Boolean confirmations must be
    explicitly supplied; no inference from future candles is performed here.
    """
    symbol = str(symbol).upper()
    profile = ASSET_PROFILES.get(symbol, {"family": "generic_adaptive", "min_score": 75, "max_atr_pct": 6.0})
    required = ("price", "ema20_1h", "ema50_1h", "ema20_4h", "ema50_4h", "rsi_1h", "atr_1h")
    missing = [key for key in required if _num(features, key) is None]
    if missing:
        return {"model_version": MODEL_VERSION, "symbol": symbol, "signal": "NO-TRADE",
                "score": 0, "status": "INSUFFICIENT_DATA", "missing": missing,
                "components": {}, "reasons": ["Missing required features"]}

    price = _num(features, "price")
    e20 = _num(features, "ema20_1h")
    e50 = _num(features, "ema50_1h")
    e20_4 = _num(features, "ema20_4h")
    e50_4 = _num(features, "ema50_4h")
    rsi = _num(features, "rsi_1h")
    atr = _num(features, "atr_1h")
    e200 = _num(features, "ema200_1h")
    e200_4 = _num(features, "ema200_4h")
    adx = _num(features, "adx_1h")
    vol = _num(features, "vol_ratio")
    vwap = _num(features, "vwap_1h")
    support = _num(features, "support")
    resistance = _num(features, "resistance")
    funding = _num(features, "funding_rate")
    oi = _num(features, "oi_change_pct")
    momentum = _num(features, "momentum_pct")
    spread = _num(features, "spread_bps")
    atr_pct = _num(features, "atr_pct")
    if price is None or atr is None or price <= 0 or atr <= 0:
        return {"model_version": MODEL_VERSION, "symbol": symbol, "signal": "NO-TRADE",
                "score": 0, "status": "INVALID_PRICE_OR_ATR", "missing": [],
                "components": {}, "reasons": ["Price and ATR must be positive"]}

    if features.get("candle_closed") is not True or features.get("data_fresh") is not True:
        return {"model_version": MODEL_VERSION, "symbol": symbol, "signal": "NO-TRADE",
                "score": 0, "status": "STALE_OR_OPEN_CANDLE", "missing": [],
                "components": {}, "reasons": ["Only fresh, closed-candle data may produce a candidate"]}
    if atr_pct is not None and atr_pct > profile["max_atr_pct"]:
        return {"model_version": MODEL_VERSION, "symbol": symbol, "signal": "NO-TRADE",
                "score": 0, "status": "VOLATILITY_BLOCK", "missing": [],
                "components": {}, "reasons": ["ATR% exceeds asset profile limit"]}
    if spread is not None and spread > 15:
        return {"model_version": MODEL_VERSION, "symbol": symbol, "signal": "NO-TRADE",
                "score": 0, "status": "SPREAD_BLOCK", "missing": [],
                "components": {}, "reasons": ["Spread exceeds 15 bps research guardrail"]}

    bull = price > e20_4 > e50_4
    bear = price < e20_4 < e50_4
    if e200_4 is not None:
        bull = bull and price > e200_4
        bear = bear and price < e200_4
    if features.get("market_regime") in ("RANGE", "HIGH_VOL_UNSTABLE", "UNKNOWN"):
        regime_factor = 0.65 if features.get("market_regime") == "RANGE" else 0.35
    else:
        regime_factor = 1.0

    # Model 1: momentum + funding. Funding is a crowding/context input, not
    # treated as guaranteed carry. Sign is direction-aware.
    long_funding = 50.0
    short_funding = 50.0
    if funding is not None:
        long_funding += max(-25.0, min(25.0, -funding * 25000))
        short_funding += max(-25.0, min(25.0, funding * 25000))
    if momentum is not None:
        long_mom = _clamp(50 + momentum * 20)
        short_mom = _clamp(50 - momentum * 20)
    else:
        long_mom = 50.0 if price > e20 else 35.0
        short_mom = 50.0 if price < e20 else 35.0
    if oi is not None and momentum is not None:
        # Rising OI supports a move only when price momentum agrees; otherwise it
        # is a possible crowded/fragile move and contributes no bonus.
        oi_bonus_long = 8 if oi > 0 and momentum > 0 else 0
        oi_bonus_short = 8 if oi > 0 and momentum < 0 else 0
    else:
        oi_bonus_long = oi_bonus_short = 0
    m1_long = _clamp(0.65 * long_mom + 0.35 * long_funding + oi_bonus_long)
    m1_short = _clamp(0.65 * short_mom + 0.35 * short_funding + oi_bonus_short)

    # Model 2: MACD/EMA trend proxy + breakout/retest. Explicit MACD may be
    # supplied as macd_hist; otherwise use EMA alignment without fabricating MACD.
    macd_hist = _num(features, "macd_hist")
    macd_long = 15 if macd_hist is not None and macd_hist > 0 else 0
    macd_short = 15 if macd_hist is not None and macd_hist < 0 else 0
    long_breakout = features.get("breakout_up") is True
    short_breakout = features.get("breakout_down") is True
    long_retest = features.get("retest_long") is True
    short_retest = features.get("retest_short") is True
    m2_long = _clamp((35 if bull else 0) + (25 if price > e20 > e50 else 0)
                     + (25 if long_breakout else 0) + (10 if long_retest else 0) + macd_long)
    m2_short = _clamp((35 if bear else 0) + (25 if price < e20 < e50 else 0)
                      + (25 if short_breakout else 0) + (10 if short_retest else 0) + macd_short)

    # Model 3: confluence. Indicator agreement is useful, but no indicator alone
    # can create a trade. Volume, VWAP, ADX, CVD and RSI are independent checks.
    cvd = str(features.get("cvd_direction", "UNKNOWN")).upper()
    long_confluence = (20 if bull else 0) + (15 if rsi is not None and 48 <= rsi <= 68 else 0)
    short_confluence = (20 if bear else 0) + (15 if rsi is not None and 32 <= rsi <= 52 else 0)
    long_confluence += 15 if vol is not None and vol >= 1.1 else 0
    short_confluence += 15 if vol is not None and vol >= 1.1 else 0
    long_confluence += 10 if vwap is not None and price > vwap else 0
    short_confluence += 10 if vwap is not None and price < vwap else 0
    long_confluence += 10 if adx is not None and adx >= 18 else 0
    short_confluence += 10 if adx is not None and adx >= 18 else 0
    long_confluence += 10 if cvd == "UP" else 0
    short_confluence += 10 if cvd == "DOWN" else 0
    long_confluence += 10 if e200 is not None and price > e200 else 0
    short_confluence += 10 if e200 is not None and price < e200 else 0
    m3_long = _clamp(long_confluence)
    m3_short = _clamp(short_confluence)

    # Model 4: coin-specific priors. They adjust strategy emphasis; they do not
    # hardcode a winning direction or bypass validation.
    family = profile["family"]
    if family == "trend_breakout_retest":
        m4_long = _clamp((30 if bull else 0) + (30 if long_retest else 0)
                         + (20 if long_breakout else 0) + (20 if price > e20 else 0))
        m4_short = _clamp((30 if bear else 0) + (30 if short_retest else 0)
                          + (20 if short_breakout else 0) + (20 if price < e20 else 0))
    elif family == "momentum_pullback_breakout":
        m4_long = _clamp((35 if bull else 0) + (25 if long_mom > 55 else 0)
                         + (20 if long_retest else 0) + (20 if long_breakout else 0))
        m4_short = _clamp((35 if bear else 0) + (25 if short_mom > 55 else 0)
                          + (20 if short_retest else 0) + (20 if short_breakout else 0))
    elif family == "conditional_trend":
        m4_long = _clamp((40 if bull else 0) + (30 if long_retest else 0) + (30 if long_breakout else 0))
        m4_short = _clamp((40 if bear else 0) + (30 if short_retest else 0) + (30 if short_breakout else 0))
    elif family == "frozen_external_model":
        # The existing frozen XRP model remains authoritative; this hybrid
        # returns context only and deliberately does not issue an XRP trade.
        return {"model_version": MODEL_VERSION, "symbol": symbol, "signal": "NO-TRADE",
                "score": 0, "status": "EXTERNAL_FROZEN_MODEL_REQUIRED", "missing": [],
                "components": {"family": family}, "reasons": ["Preserve the frozen XRP model; hybrid cannot override it"]}
    else:
        m4_long = _clamp((25 if bull else 0) + (25 if long_retest else 0)
                         + (25 if long_breakout else 0) + (25 if long_mom > 55 else 0))
        m4_short = _clamp((25 if bear else 0) + (25 if short_retest else 0)
                          + (25 if short_breakout else 0) + (25 if short_mom > 55 else 0))

    long_parts = {"momentum_funding": round(m1_long, 2), "trend_breakout": round(m2_long, 2),
                  "confluence": round(m3_long, 2), "asset_adaptive": round(m4_long, 2)}
    short_parts = {"momentum_funding": round(m1_short, 2), "trend_breakout": round(m2_short, 2),
                   "confluence": round(m3_short, 2), "asset_adaptive": round(m4_short, 2)}
    long_score = round(sum(long_parts[k] * WEIGHTS[k] for k in WEIGHTS) * regime_factor)
    short_score = round(sum(short_parts[k] * WEIGHTS[k] for k in WEIGHTS) * regime_factor)

    # Do not buy directly into nearby resistance or short directly into nearby support.
    long_room = (resistance - price) / atr if resistance is not None and resistance > price else None
    short_room = (price - support) / atr if support is not None and support < price else None
    long_block = long_room is not None and long_room < 0.75
    short_block = short_room is not None and short_room < 0.75
    if long_block:
        long_score = min(long_score, 49)
    if short_block:
        short_score = min(short_score, 49)

    min_score = int(profile["min_score"])
    edge = abs(long_score - short_score)
    if max(long_score, short_score) < min_score or edge < 8:
        signal = "NO-TRADE"
        score = max(long_score, short_score)
        status = "BELOW_THRESHOLD_OR_DIRECTION_CONFLICT"
    elif long_score > short_score:
        signal, score, status = "LONG", long_score, "RESEARCH_CANDIDATE"
    else:
        signal, score, status = "SHORT", short_score, "RESEARCH_CANDIDATE"

    reasons = []
    if long_block:
        reasons.append("Long blocked: resistance is less than 0.75 ATR away")
    if short_block:
        reasons.append("Short blocked: support is less than 0.75 ATR away")
    if features.get("market_regime") == "RANGE":
        reasons.append("Range regime: score discounted")
    if features.get("market_regime") == "HIGH_VOL_UNSTABLE":
        reasons.append("Unstable high-volatility regime: score heavily discounted")
    return {
        "model_version": MODEL_VERSION, "symbol": symbol, "family": family,
        "signal": signal, "score": score, "status": status, "missing": [],
        "components": {"long": long_parts, "short": short_parts,
                       "long_score": long_score, "short_score": short_score,
                       "weights": WEIGHTS, "regime_factor": regime_factor},
        "risk": {"atr": atr, "stop_atr_default": 1.5, "target_r_default": 2.0,
                 "long_resistance_room_atr": long_room, "short_support_room_atr": short_room},
        "reasons": reasons,
    }
