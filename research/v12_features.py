"""Build causal V12 feature rows from validated 1h/4h candle CSVs.

All indicators use candles whose close timestamps are <= the signal candle close.
Funding is joined as-of the signal close; future funding settlements are never used.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

from research.run_backtest import load_csv, load_funding_csv, write_json_atomic


def _ema(values, period):
    if len(values) < period:
        return None
    alpha = 2.0 / (period + 1)
    value = sum(values[:period]) / period
    for item in values[period:]:
        value = alpha * item + (1 - alpha) * value
    return value


def _rsi(values, period=14):
    if len(values) < period + 1:
        return None
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(x, 0.0) for x in changes]
    losses = [max(-x, 0.0) for x in changes]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(changes)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def _atr(rows, period=14):
    if len(rows) < period + 1:
        return None
    tr = []
    for i in range(1, len(rows)):
        prev_close = rows[i - 1]["c"]
        tr.append(max(rows[i]["h"] - rows[i]["l"],
                      abs(rows[i]["h"] - prev_close),
                      abs(rows[i]["l"] - prev_close)))
    return sum(tr[-period:]) / period


def _adx(rows, period=14):
    if len(rows) < period * 2 + 1:
        return None
    tr_values, plus_values, minus_values = [], [], []
    for i in range(1, len(rows)):
        up = rows[i]["h"] - rows[i - 1]["h"]
        down = rows[i - 1]["l"] - rows[i]["l"]
        tr_values.append(max(rows[i]["h"] - rows[i]["l"],
                             abs(rows[i]["h"] - rows[i - 1]["c"]),
                             abs(rows[i]["l"] - rows[i - 1]["c"])))
        plus_values.append(up if up > down and up > 0 else 0.0)
        minus_values.append(down if down > up and down > 0 else 0.0)
    dx = []
    for end in range(period, len(tr_values) + 1):
        tr = sum(tr_values[end-period:end]) / period
        if tr <= 0:
            continue
        pdi = 100 * (sum(plus_values[end-period:end]) / period) / tr
        mdi = 100 * (sum(minus_values[end-period:end]) / period) / tr
        dx.append(100 * abs(pdi - mdi) / max(pdi + mdi, 1e-12))
    return sum(dx[-period:]) / len(dx[-period:]) if dx else None


def _macd_hist(values, fast=12, slow=26, signal=9):
    if len(values) < slow + signal:
        return None
    fast_alpha, slow_alpha = 2 / (fast + 1), 2 / (slow + 1)
    fast_value = sum(values[:fast]) / fast
    slow_value = sum(values[:slow]) / slow
    macd_values = []
    # Compute aligned EMA series from the first index where both EMA seeds exist.
    fast_series = [None] * (fast - 1) + [fast_value]
    for value in values[fast:]:
        fast_value = fast_alpha * value + (1 - fast_alpha) * fast_value
        fast_series.append(fast_value)
    slow_series = [None] * (slow - 1) + [slow_value]
    for value in values[slow:]:
        slow_value = slow_alpha * value + (1 - slow_alpha) * slow_value
        slow_series.append(slow_value)
    for a, b in zip(fast_series, slow_series):
        if a is not None and b is not None:
            macd_values.append(a - b)
    if len(macd_values) < signal:
        return None
    signal_value = sum(macd_values[:signal]) / signal
    alpha = 2 / (signal + 1)
    for value in macd_values[signal:]:
        signal_value = alpha * value + (1 - alpha) * signal_value
    return macd_values[-1] - signal_value


def _vwap(rows, period=48):
    sample = rows[-period:]
    volume = sum(row.get("q", 0.0) for row in sample)
    if volume <= 0:
        return None
    return sum(((row["h"] + row["l"] + row["c"]) / 3) * row.get("q", 0.0)
               for row in sample) / volume


def _funding_asof(events, timestamp):
    found = None
    for event in events or []:
        if event["funding_time"] > timestamp:
            break
        found = event["funding_rate"]
    return found


def build_v12_features(candles_1h, candles_4h, funding_events=None):
    """Return one causally aligned feature row per 1h candle with sufficient history."""
    output = []
    funding_events = funding_events or []
    funding_events = sorted(funding_events, key=lambda x: x["funding_time"])
    j4 = 0
    closed_4h = []
    for i, candle in enumerate(candles_1h):
        close_ts = candle["t"] + 3_600_000
        while j4 < len(candles_4h) and candles_4h[j4]["t"] + 14_400_000 <= close_ts:
            closed_4h.append(candles_4h[j4])
            j4 += 1
        h1 = candles_1h[:i + 1]
        c1 = [x["c"] for x in h1]
        c4 = [x["c"] for x in closed_4h]
        if len(h1) < 50 or len(closed_4h) < 50:
            continue

        price = candle["c"]
        atr_value = _atr(h1)
        if atr_value is None or atr_value <= 0:
            continue
        ema20_1h, ema50_1h, ema200_1h = _ema(c1, 20), _ema(c1, 50), _ema(c1, 200)
        ema20_4h, ema50_4h, ema200_4h = _ema(c4, 20), _ema(c4, 50), _ema(c4, 200)
        if ema20_1h is None or ema50_1h is None or ema20_4h is None or ema50_4h is None:
            continue
        rsi_value = _rsi(c1)
        if rsi_value is None:
            continue
        lookback = h1[-49:-1]
        support = min(x["l"] for x in lookback) if lookback else None
        resistance = max(x["h"] for x in lookback) if lookback else None
        previous_close = h1[-2]["c"]
        regime = "BULL" if price > ema20_4h > ema50_4h else (
            "BEAR" if price < ema20_4h < ema50_4h else "RANGE")
        if ema200_4h is not None:
            if regime == "BULL" and price <= ema200_4h:
                regime = "RANGE"
            elif regime == "BEAR" and price >= ema200_4h:
                regime = "RANGE"
        volume_base = [x.get("q", 0.0) for x in h1[-21:-1]]
        volume_mean = sum(volume_base) / len(volume_base) if volume_base else 0.0
        vol_ratio = candle.get("q", 0.0) / volume_mean if volume_mean > 0 else None
        output.append({
            "t": candle["t"], "o": candle["o"], "h": candle["h"], "l": candle["l"], "c": price,
            "ema20_1h": ema20_1h, "ema50_1h": ema50_1h, "ema200_1h": ema200_1h,
            "ema20_4h": ema20_4h, "ema50_4h": ema50_4h, "ema200_4h": ema200_4h,
            "rsi_1h": rsi_value, "atr_1h": atr_value, "atr_pct": atr_value / price * 100,
            "adx_1h": _adx(h1), "vol_ratio": vol_ratio, "vwap_1h": _vwap(h1),
            "support": support, "resistance": resistance,
            "funding_rate": _funding_asof(funding_events, close_ts), "oi_change_pct": None,
            "momentum_pct": (price / c1[-4] - 1) * 100 if len(c1) >= 4 and c1[-4] else None,
            "macd_hist": _macd_hist(c1),
            "breakout_up": bool(resistance is not None and price > resistance and previous_close <= resistance),
            "breakout_down": bool(support is not None and price < support and previous_close >= support),
            "retest_long": bool(support is not None and abs(price - support) <= 0.4 * atr_value and price >= previous_close),
            "retest_short": bool(resistance is not None and abs(price - resistance) <= 0.4 * atr_value and price <= previous_close),
            "cvd_direction": "UNKNOWN", "market_regime": regime,
            "data_fresh": True, "candle_closed": True, "spread_bps": None,
        })
    return output


def write_feature_csv(path, rows):
    if not rows:
        raise ValueError("no feature rows produced; provide longer aligned candle histories")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0])
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candles-1h", required=True, help="validated 1h OHLCV CSV")
    parser.add_argument("--candles-4h", required=True, help="validated 4h OHLCV CSV")
    parser.add_argument("--funding-csv", help="optional historical funding settlement CSV")
    parser.add_argument("--output", required=True, help="output V12 feature CSV")
    args = parser.parse_args()
    one_hour = load_csv(args.candles_1h, 3_600_000)
    four_hour = load_csv(args.candles_4h, 14_400_000)
    funding = load_funding_csv(args.funding_csv) if args.funding_csv else []
    if funding and funding[0]["symbol"] == "":
        raise ValueError("funding CSV must contain a symbol")
    features = build_v12_features(one_hour, four_hour, funding)
    write_feature_csv(args.output, features)
    print(json.dumps({"status": "RESEARCH_ONLY", "input_1h_bars": len(one_hour),
                      "input_4h_bars": len(four_hour), "feature_rows": len(features),
                      "output": str(Path(args.output).resolve()),
                      "funding_rows": len(funding),
                      "limitations": ["historical OI change unavailable unless separately aligned",
                                      "CVD unavailable; set to UNKNOWN",
                                      "spread unavailable; no spread filter applied"]}, indent=2))


if __name__ == "__main__":
    main()
