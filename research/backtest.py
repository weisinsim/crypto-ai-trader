"""Conservative OHLCV backtest primitives; research use only, not a live signal engine."""
from __future__ import annotations

from math import isfinite
from typing import Iterable


REQUIRED = ("t", "o", "h", "l", "c")


def validate_candles(rows: Iterable[dict], interval_ms: int | None = None) -> list[dict]:
    """Validate chronological OHLCV rows and optionally require contiguous timestamps."""
    data = list(rows)
    previous = None
    for i, row in enumerate(data):
        missing = [key for key in REQUIRED if key not in row]
        if missing:
            raise ValueError(f"row {i} missing fields: {missing}")
        ts = int(row["t"])
        vals = {key: float(row[key]) for key in ("o", "h", "l", "c")}
        if not all(isfinite(v) and v > 0 for v in vals.values()):
            raise ValueError(f"row {i} contains invalid OHLC values")
        if vals["h"] < max(vals["o"], vals["c"], vals["l"]):
            raise ValueError(f"row {i} has inconsistent high")
        if vals["l"] > min(vals["o"], vals["c"], vals["h"]):
            raise ValueError(f"row {i} has inconsistent low")
        if previous is not None:
            if ts <= previous:
                raise ValueError(f"row {i} timestamps are not strictly increasing")
            if interval_ms is not None and ts - previous != interval_ms:
                raise ValueError(f"row {i} breaks expected interval continuity")
        previous = ts
    return data


def _ema(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    value = sum(values[:period]) / period
    out[period - 1] = value
    alpha = 2.0 / (period + 1)
    for i in range(period, len(values)):
        value = values[i] * alpha + value * (1 - alpha)
        out[i] = value
    return out


def _atr(rows: list[dict], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(rows)
    tr: list[float] = []
    for i, row in enumerate(rows):
        prev_close = float(rows[i - 1]["c"]) if i else float(row["c"])
        tr.append(max(float(row["h"]) - float(row["l"]),
                      abs(float(row["h"]) - prev_close),
                      abs(float(row["l"]) - prev_close)))
        if i >= period - 1:
            out[i] = sum(tr[i - period + 1:i + 1]) / period
    return out


def run_ema_cross_backtest(
    candles: Iterable[dict],
    *,
    fast: int = 20,
    slow: int = 50,
    atr_period: int = 14,
    stop_atr: float = 1.5,
    target_atr: float = 2.0,
    fee_rate: float = 0.0005,
    slippage_rate: float = 0.0002,
    funding_rate_per_bar: float = 0.0,
    allow_short: bool = True,
) -> dict:
    """Next-open EMA cross baseline with fixed-risk exits and conservative same-bar ordering.

    Signals are computed from completed bar i-1 and filled at bar i open.
    If a bar touches both stop and target, stop is assumed first. Fees are
    charged on entry and exit notional; funding is charged per held bar.
    Returns percentage-equity results with one unit of initial equity and no leverage.
    """
    rows = validate_candles(candles)
    if fast < 2 or slow <= fast or atr_period < 1:
        raise ValueError("require 2 <= fast < slow and atr_period >= 1")
    if stop_atr <= 0 or target_atr <= 0:
        raise ValueError("stop_atr and target_atr must be positive")
    if min(fee_rate, slippage_rate) < 0:
        raise ValueError("fee and slippage rates cannot be negative")
    if len(rows) < slow + 2:
        return {"status": "INSUFFICIENT_DATA", "bars": len(rows), "trades": 0,
                "net_return_pct": 0.0, "max_drawdown_pct": 0.0, "win_rate_pct": None,
                "profit_factor": None, "expectancy_pct": None, "trade_log": []}

    closes = [float(r["c"]) for r in rows]
    fast_ema, slow_ema = _ema(closes, fast), _ema(closes, slow)
    atrs = _atr(rows, atr_period)
    equity = 1.0
    peak = equity
    max_dd = 0.0
    position = None
    trades = []
    equity_curve = [equity]
    fee_slip = fee_rate + slippage_rate

    for i in range(1, len(rows)):
        bar = rows[i]
        # A position opened at a prior bar's open is checked against this bar.
        if position is not None:
            position["bars_held"] += 1
            exit_price = None
            reason = None
            if position["side"] == 1:
                stop_hit = float(bar["l"]) <= position["stop"]
                target_hit = float(bar["h"]) >= position["target"]
            else:
                stop_hit = float(bar["h"]) >= position["stop"]
                target_hit = float(bar["l"]) <= position["target"]
            if stop_hit:  # conservative if stop and target both touch in one candle
                exit_price, reason = position["stop"], "STOP"
            elif target_hit:
                exit_price, reason = position["target"], "TARGET"
            elif i == len(rows) - 1:
                exit_price, reason = float(bar["c"]), "END_OF_DATA"
            if exit_price is not None:
                side = position["side"]
                # Adverse slippage on both fills; long sells lower, short buys higher.
                exit_fill = exit_price * (1 - fee_slip if side == 1 else 1 + fee_slip)
                gross = side * (exit_fill - position["entry_fill"]) / position["entry_fill"]
                funding_cost = abs(position["entry_fill"] * position["qty"]) * funding_rate_per_bar * position["bars_held"]
                pnl = position["equity_before"] * gross - position["entry_fee"] - position["equity_before"] * abs(funding_rate_per_bar) * position["bars_held"]
                equity += pnl
                ret_pct = (equity / position["equity_before"] - 1) * 100
                trades.append({"side": "LONG" if side == 1 else "SHORT",
                               "entry_time": position["entry_time"], "exit_time": int(bar["t"]),
                               "entry": round(position["entry_fill"], 8), "exit": round(exit_fill, 8),
                               "reason": reason, "bars_held": position["bars_held"],
                               "return_pct": round(ret_pct, 6), "pnl_equity": round(pnl, 8)})
                position = None

        # Signal is known only after the previous bar closes; fill at current open.
        if position is None and fast_ema[i - 1] is not None and slow_ema[i - 1] is not None and fast_ema[i - 2] is not None and slow_ema[i - 2] is not None and atrs[i - 1]:
            prior_diff = fast_ema[i - 2] - slow_ema[i - 2]
            current_diff = fast_ema[i - 1] - slow_ema[i - 1]
            side = 1 if prior_diff <= 0 < current_diff else (-1 if prior_diff >= 0 > current_diff and allow_short else 0)
            if side:
                open_price = float(bar["o"])
                risk = float(atrs[i - 1]) * stop_atr
                entry_fill = open_price * (1 + fee_slip if side == 1 else 1 - fee_slip)
                position = {"side": side, "entry_fill": entry_fill,
                            "stop": open_price - side * risk,
                            "target": open_price + side * risk * target_atr,
                            "entry_time": int(bar["t"]), "bars_held": 0,
                            "equity_before": equity, "entry_fee": equity * fee_rate,
                            "qty": equity / max(entry_fill, 1e-12)}
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak if peak else 0)
        equity_curve.append(equity)

    if position is not None:
        bar = rows[-1]
        exit_fill = float(bar["c"]) * (1 - fee_slip if position["side"] == 1 else 1 + fee_slip)
        side = position["side"]
        gross = side * (exit_fill - position["entry_fill"]) / position["entry_fill"]
        pnl = position["equity_before"] * gross - position["entry_fee"]
        equity += pnl
        trades.append({"side": "LONG" if side == 1 else "SHORT",
                       "entry_time": position["entry_time"], "exit_time": int(bar["t"]),
                       "entry": round(position["entry_fill"], 8), "exit": round(exit_fill, 8),
                       "reason": "END_OF_DATA", "bars_held": position["bars_held"],
                       "return_pct": round((equity / position["equity_before"] - 1) * 100, 6),
                       "pnl_equity": round(pnl, 8)})

    wins = [t["pnl_equity"] for t in trades if t["pnl_equity"] > 0]
    losses = [-t["pnl_equity"] for t in trades if t["pnl_equity"] < 0]
    gross_loss = sum(losses)
    return {"status": "COMPLETED", "bars": len(rows), "trades": len(trades),
            "net_return_pct": round((equity - 1) * 100, 4),
            "max_drawdown_pct": round(max_dd * 100, 4),
            "win_rate_pct": round(len(wins) / len(trades) * 100, 2) if trades else None,
            "profit_factor": round(sum(wins) / gross_loss, 4) if gross_loss else (None if not wins else "INF"),
            "expectancy_pct": round(sum(t["return_pct"] for t in trades) / len(trades), 4) if trades else None,
            "equity_final": round(equity, 8), "trade_log": trades}
