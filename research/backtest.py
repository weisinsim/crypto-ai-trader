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
    trend_period: int = 0,
    atr_period: int = 14,
    stop_atr: float = 1.5,
    target_atr: float = 2.0,
    fee_rate: float = 0.0005,
    slippage_rate: float = 0.0002,
    funding_rate_per_bar: float = 0.0,
    funding_rates: Iterable[dict] | None = None,
    allow_short: bool = True,
    risk_fraction: float = 0.01,
    trade_start_ts: int | None = None,
) -> dict:
    """Next-open EMA cross baseline with fixed-risk exits and conservative same-bar ordering.

    Signals are computed from completed bar i-1 and filled at bar i open.
    Optional historical funding events are settled only when an existing position
    is open at an event timestamp; event rows require funding_time/rate/mark_price.
    To avoid silently mis-timing fees, every event must align with a candle open.
    If a bar touches both stop and target, stop is assumed first. Fees are
    charged on entry and exit notional; funding is charged per held bar.
    Returns percentage-equity results with one unit of initial equity and no leverage.
    """
    rows = validate_candles(candles)
    funding_events = {}
    if funding_rates is not None:
        candle_timestamps = {int(row["t"]) for row in rows}
        previous_funding_ts = None
        for event in sorted(list(funding_rates), key=lambda item: int(item["funding_time"])):
            funding_ts = int(event["funding_time"])
            rate = float(event["funding_rate"])
            mark_price = float(event["mark_price"])
            if not isfinite(rate) or not isfinite(mark_price) or mark_price <= 0:
                raise ValueError("funding events require finite rates and positive finite mark prices")
            if previous_funding_ts == funding_ts:
                raise ValueError(f"duplicate funding timestamp: {funding_ts}")
            if funding_ts not in candle_timestamps:
                raise ValueError(
                    f"funding timestamp {funding_ts} does not align with a candle open; "
                    "use a finer candle interval or event-aware execution data"
                )
            funding_events[funding_ts] = {"funding_rate": rate, "mark_price": mark_price}
            previous_funding_ts = funding_ts
    if fast < 2 or slow <= fast or atr_period < 1 or trend_period < 0:
        raise ValueError("require 2 <= fast < slow, atr_period >= 1 and trend_period >= 0")
    numeric_params = {
        "stop_atr": stop_atr, "target_atr": target_atr,
        "fee_rate": fee_rate, "slippage_rate": slippage_rate,
        "funding_rate_per_bar": funding_rate_per_bar,
        "risk_fraction": risk_fraction,
    }
    if not all(isfinite(float(value)) for value in numeric_params.values()):
        raise ValueError("all numeric strategy parameters must be finite")
    if stop_atr <= 0 or target_atr <= 0:
        raise ValueError("stop_atr and target_atr must be positive")
    if fee_rate < 0 or slippage_rate < 0:
        raise ValueError("fee and slippage rates cannot be negative")
    if not 0 < risk_fraction <= 1:
        raise ValueError("risk_fraction must be in (0, 1]")
    if len(rows) < slow + 2:
        return {"status": "INSUFFICIENT_DATA", "bars": len(rows), "trades": 0,
                "net_return_pct": 0.0, "max_drawdown_pct": 0.0, "win_rate_pct": None,
                "profit_factor": None, "expectancy_pct": None, "trade_log": []}

    closes = [float(r["c"]) for r in rows]
    fast_ema, slow_ema = _ema(closes, fast), _ema(closes, slow)
    trend_ema = _ema(closes, trend_period) if trend_period >= 2 else None
    if trend_period == 1:
        raise ValueError("trend_period must be 0 (disabled) or >= 2")
    atrs = _atr(rows, atr_period)
    equity = 1.0
    peak = equity
    max_dd = 0.0
    position = None
    trades = []
    equity_curve = [{"t": int(rows[0]["t"]), "equity": equity}]
    # Slippage affects fill prices; commissions are deducted separately on both fills.

    def finalize_position(pos: dict, bar: dict, exit_price: float, reason: str, current_equity: float):
        side = pos["side"]
        # Adverse slippage on both fills; long sells lower, short buys higher.
        exit_fill = exit_price * (1 - slippage_rate if side == 1 else 1 + slippage_rate)
        gross_pnl = pos["qty"] * side * (exit_fill - pos["entry_fill"])
        exit_fee = abs(pos["qty"] * exit_fill) * fee_rate
        funding_cost = (pos.get("funding_cost", 0.0) if funding_rates is not None else
                        pos["qty"] * pos["entry_fill"] * funding_rate_per_bar * side * pos["bars_held"])
        pnl = gross_pnl - pos["entry_fee"] - exit_fee - funding_cost
        new_equity = current_equity + pnl
        trade = {
            "side": "LONG" if side == 1 else "SHORT",
            "entry_time": pos["entry_time"], "exit_time": int(bar["t"]),
            "entry": round(pos["entry_fill"], 8), "exit": round(exit_fill, 8),
            "reason": reason, "bars_held": pos["bars_held"],
            "stop": round(pos["stop"], 8), "target": round(pos["target"], 8),
            "return_pct": round((new_equity / pos["equity_before"] - 1) * 100, 6),
            "pnl_equity": round(pnl, 8),
        }
        return new_equity, trade

    for i in range(1, len(rows)):
        bar = rows[i]
        # Entry availability is decided at the bar open. A position that exits
        # intrabar must not be replaced using a signal at that same bar's open.
        can_enter_at_open = position is None
        opened_this_bar = False
        # Apply exchange funding settlement at this bar timestamp to positions
        # already open at the event; positions opened later on this bar are excluded.
        if position is not None and int(bar["t"]) in funding_events and funding_rates is not None:
            event = funding_events[int(bar["t"])]
            position["funding_cost"] = position.get("funding_cost", 0.0) + (
                position["qty"] * event["mark_price"] * event["funding_rate"] * position["side"])
        # A position carried into this candle is checked against this bar.
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
            if stop_hit:  # stop first if both barriers touch in one candle
                # Model adverse opening gaps through a stop at the worse open price.
                if position["side"] == 1 and float(bar["o"]) < position["stop"]:
                    exit_price = float(bar["o"])
                elif position["side"] == -1 and float(bar["o"]) > position["stop"]:
                    exit_price = float(bar["o"])
                else:
                    exit_price = position["stop"]
                reason = "STOP"
            elif target_hit:
                exit_price, reason = position["target"], "TARGET"
            elif i == len(rows) - 1:
                exit_price, reason = float(bar["c"]), "END_OF_DATA"
            if exit_price is not None:
                equity, trade = finalize_position(position, bar, exit_price, reason, equity)
                trades.append(trade)
                position = None

        # Signal is known only after the previous bar closes; fill at current open.
        if (can_enter_at_open and position is None and (trade_start_ts is None or int(bar["t"]) >= trade_start_ts)
                and fast_ema[i - 1] is not None and slow_ema[i - 1] is not None
                and fast_ema[i - 2] is not None and slow_ema[i - 2] is not None and atrs[i - 1]):
            prior_diff = fast_ema[i - 2] - slow_ema[i - 2]
            current_diff = fast_ema[i - 1] - slow_ema[i - 1]
            side = 1 if prior_diff <= 0 < current_diff else (-1 if prior_diff >= 0 > current_diff and allow_short else 0)
            if side and trend_ema is not None:
                trend_value = trend_ema[i - 1]
                close_value = float(rows[i - 1]["c"])
                if trend_value is None or (side == 1 and close_value <= trend_value) or (side == -1 and close_value >= trend_value):
                    side = 0
            if side:
                open_price = float(bar["o"])
                risk = float(atrs[i - 1]) * stop_atr
                entry_fill = open_price * (1 + slippage_rate if side == 1 else 1 - slippage_rate)
                # Risk-based sizing, capped at 1x equity notional (no leverage).
                qty_by_risk = equity * risk_fraction / max(risk, 1e-12)
                qty_by_notional = equity / max(entry_fill, 1e-12)
                qty = min(qty_by_risk, qty_by_notional)
                position = {"side": side, "entry_fill": entry_fill,
                            "stop": open_price - side * risk,
                            "target": open_price + side * risk * target_atr,
                            "entry_time": int(bar["t"]), "bars_held": 0,
                            "equity_before": equity, "qty": qty,
                            "entry_fee": abs(qty * entry_fill) * fee_rate}
                opened_this_bar = True

        # The position was filled at this bar's open, so its stop/target may
        # already be hit within this same OHLC candle. Resolve stop first.
        if opened_this_bar and position is not None:
            if position["side"] == 1:
                stop_hit = float(bar["l"]) <= position["stop"]
                target_hit = float(bar["h"]) >= position["target"]
            else:
                stop_hit = float(bar["h"]) >= position["stop"]
                target_hit = float(bar["l"]) <= position["target"]
            if stop_hit or target_hit:
                reason = "STOP" if stop_hit else "TARGET"
                exit_price = position["stop"] if stop_hit else position["target"]
                equity, trade = finalize_position(position, bar, exit_price, reason, equity)
                trades.append(trade)
                position = None

        # Mark open positions to market so drawdown includes unrealized losses.
        marked_equity = equity
        if position is not None:
            mark_price = float(bar["c"])
            unrealized_pnl = position["qty"] * position["side"] * (mark_price - position["entry_fill"])
            estimated_exit_fee = abs(position["qty"] * mark_price) * fee_rate
            accrued_funding = (position.get("funding_cost", 0.0) if funding_rates is not None else
                               position["qty"] * position["entry_fill"] * funding_rate_per_bar
                               * position["side"] * position["bars_held"])
            marked_equity += (unrealized_pnl - position["entry_fee"]
                              - estimated_exit_fee - accrued_funding)
        peak = max(peak, marked_equity)
        max_dd = max(max_dd, (peak - marked_equity) / peak if peak else 0)
        equity_curve.append({"t": int(bar["t"]), "equity": marked_equity})

    if position is not None:
        bar = rows[-1]
        equity, trade = finalize_position(position, bar, float(bar["c"]), "END_OF_DATA", equity)
        trades.append(trade)
        # Include the final liquidation value in maximum drawdown.
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak if peak else 0.0)
        if equity_curve and equity_curve[-1]["t"] == int(bar["t"]):
            equity_curve[-1]["equity"] = equity

    wins = [t["pnl_equity"] for t in trades if t["pnl_equity"] > 0]
    losses = [-t["pnl_equity"] for t in trades if t["pnl_equity"] < 0]
    gross_loss = sum(losses)
    return {"status": "COMPLETED", "bars": len(rows), "trades": len(trades),
            "net_return_pct": round((equity - 1) * 100, 4),
            "max_drawdown_pct": round(max_dd * 100, 4),
            "win_rate_pct": round(len(wins) / len(trades) * 100, 2) if trades else None,
            "profit_factor": round(sum(wins) / gross_loss, 4) if gross_loss else (None if not wins else "INF"),
            "expectancy_pct": round(sum(t["return_pct"] for t in trades) / len(trades), 4) if trades else None,
            "equity_final": round(equity, 8), "equity_curve": equity_curve, "trade_log": trades}
