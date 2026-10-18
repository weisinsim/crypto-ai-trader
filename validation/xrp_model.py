"""Shared frozen XRP V27.2 decision logic for live dashboard and historical replay.

This module intentionally preserves the existing thresholds and LONG_ONLY behavior.
It is a signal calculation helper only; it never places orders.
"""


def evaluate_xrp_signal(
    closes_1h,
    e20_1h,
    e20_4h,
    e50_4h,
    rsi_1h,
    atr_1h,
    resistance,
    params,
):
    """Return the XRP signal and risk levels using the dashboard's exact rules."""
    price = closes_1h[-1]
    previous = closes_1h[-2] if len(closes_1h) > 1 else price
    slope = (e20_4h / e50_4h - 1) if e20_4h and e50_4h else 0
    atr_pct = (atr_1h / price) if atr_1h and price else 0
    shock = abs(price / previous - 1) if previous else 0
    kill = (
        abs(slope) > params["kill_slope"]
        or atr_pct > params["atr_max"]
        or shock > params["shock_max"]
    )

    dev = ((price - e20_1h) / atr_1h) if atr_1h and e20_1h else 0
    mean_reversion = (
        rsi_1h is not None and rsi_1h <= 35 and dev <= -0.5 and price > previous
    )
    trend = e20_4h > e50_4h and e20_1h > e50_1h
    pullback = (
        trend and len(closes_1h) >= 3 and closes_1h[-2] <= closes_1h[-3]
        and price > e20_1h and rsi_1h is not None and rsi_1h <= 55
    )
    breakout = (
        trend and resistance is not None and price > resistance and price > previous
    )

    if not kill and (mean_reversion or pullback or breakout):
        signal = "LONG"
    elif not kill and ((rsi_1h is not None and rsi_1h <= 40) or trend):
        signal = "WATCH-LONG"
    else:
        signal = "NO-TRADE"

    direction = 1 if signal in ("LONG", "WATCH-LONG") else 0
    entry = stop = target1 = target2 = None
    if direction:
        distance = atr_1h or price * 0.01
        entry = price
        stop = price - params["sl_atr"] * distance
        target1 = price + params["tp_atr"] * distance
        target2 = price + 3.5 * distance

    score = 43 if signal == "WATCH-LONG" else 75 if signal == "LONG" else 0
    if kill:
        score = 0
        signal = "NO-TRADE"
        direction = 0
        entry = stop = target1 = target2 = None

    return {
        "signal": signal,
        "direction": direction,
        "entry": entry,
        "stop": stop,
        "target1": target1,
        "target2": target2,
        "score": score,
        "kill": kill,
        "slope": slope,
        "atr_pct": atr_pct,
        "shock": shock,
    }
