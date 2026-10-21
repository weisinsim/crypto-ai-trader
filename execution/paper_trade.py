"""Deterministic, fee-aware paper-trade lifecycle for alert research.

This module is a simulation ledger only. It never connects to an exchange or
places orders. Callers must provide already-validated OHLC candles in ascending
timestamp order; stop-loss and take-profit collisions within one candle use a
conservative stop-first assumption.
"""
from __future__ import annotations

import math


def _finite_positive(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(float(value)) and float(value) > 0)


def open_paper_trade(event, *, quantity, fee_rate=0.0005, opened_at_ms):
    """Create a paper position from a notification event, never an order."""
    if not isinstance(event, dict) or event.get("eligible") is not True:
        raise ValueError("event_not_eligible")
    if event.get("notification_only") is not True or event.get("auto_trade_authorized") is not False:
        raise ValueError("event_not_notification_only")
    side = event.get("side")
    if side not in ("LONG", "SHORT"):
        raise ValueError("invalid_side")
    entry = event.get("entry_price", event.get("entry"))
    stop = event.get("stop_loss")
    if stop is None:
        stop = event.get("sl")
    tp1 = event.get("take_profit_1")
    if tp1 is None:
        tp1 = event.get("tp1")
    tp2 = event.get("take_profit_2")
    if tp2 is None:
        tp2 = event.get("tp2")
    if not all(_finite_positive(x) for x in (entry, stop, tp1, tp2, quantity)):
        raise ValueError("invalid_trade_numbers")
    if (side == "LONG" and not stop < entry < tp1 <= tp2) or (
        side == "SHORT" and not tp2 <= tp1 < entry < stop
    ):
        raise ValueError("invalid_stop_geometry")
    if (not isinstance(fee_rate, (int, float)) or isinstance(fee_rate, bool)
            or not math.isfinite(float(fee_rate)) or fee_rate < 0 or fee_rate >= 0.1
            or isinstance(opened_at_ms, bool) or not isinstance(opened_at_ms, int)
            or opened_at_ms <= 0):
        raise ValueError("invalid_cost_or_timestamp")
    return {
        "event_id": event.get("event_id"),
        "symbol": event.get("symbol"),
        "side": side,
        "status": "OPEN",
        "entry_price": float(entry),
        "stop_loss": float(stop),
        "take_profit_1": float(tp1),
        "take_profit_2": float(tp2),
        "quantity_initial": float(quantity),
        "quantity_remaining": float(quantity),
        "quantity_closed": 0.0,
        "realized_gross_pnl": 0.0,
        "fees_paid": float(quantity) * float(entry) * float(fee_rate),
        "fee_rate": float(fee_rate),
        "opened_at_ms": opened_at_ms,
        "last_candle_ms": None,
        "closed_at_ms": None,
        "exit_reason": None,
        "trailing_stop": None,\n        "tp1_taken": False,
        "funding_paid": 0.0,
        "funding_events": 0,
        "last_funding_at_ms": None,
    }


def _pnl(side, entry, exit_price, quantity):
    direction = 1.0 if side == "LONG" else -1.0
    return direction * (exit_price - entry) * quantity


def apply_funding(trade, *, rate, mark_price, timestamp_ms):
    """Apply a simulated signed funding rate; positive rates charge longs."""
    if trade.get("status") != "OPEN":
        raise ValueError("trade_not_open")
    last_funding = trade.get("last_funding_at_ms")
    if (not isinstance(rate, (int, float)) or isinstance(rate, bool)
            or not math.isfinite(rate) or abs(rate) > 0.05
            or not _finite_positive(mark_price)
            or isinstance(timestamp_ms, bool) or not isinstance(timestamp_ms, int)
            or timestamp_ms < trade["opened_at_ms"]
            or (last_funding is not None and timestamp_ms <= last_funding)):
        raise ValueError("invalid_funding_input")
    signed = 1.0 if trade["side"] == "LONG" else -1.0
    cost = float(mark_price) * trade["quantity_remaining"] * float(rate) * signed
    trade["funding_paid"] += cost
    trade["funding_events"] += 1
    trade["last_funding_at_ms"] = timestamp_ms
    return cost


def update_trailing_stop(trade, *, candidate_stop):
    """Only tighten a stop; never loosen risk after entry."""
    if trade.get("status") != "OPEN" or not _finite_positive(candidate_stop):
        raise ValueError("invalid_trailing_stop")
    current = trade.get("trailing_stop")
    if trade["side"] == "LONG":
        # Never place a trailing stop below the original protective stop.
        if candidate_stop <= trade["stop_loss"]:
            return False
        if current is None or candidate_stop > current:
            trade["trailing_stop"] = float(candidate_stop)
            return True
    else:
        # For shorts, lower stops are tighter; never loosen beyond original SL.
        if candidate_stop >= trade["stop_loss"]:
            return False
        if current is None or candidate_stop < current:
            trade["trailing_stop"] = float(candidate_stop)
            return True
    return False


def process_candle(trade, candle, *, timestamp_ms):
    """Process one closed OHLC candle; conservative stop-first collision rule."""
    if trade.get("status") != "OPEN":
        return {"status": trade.get("status"), "action": "IGNORED_CLOSED"}
    if (not isinstance(candle, dict)
            or not all(_finite_positive(candle.get(k)) for k in ("open", "high", "low", "close"))
            or candle["low"] > min(candle["open"], candle["close"])
            or candle["high"] < max(candle["open"], candle["close"])
            or candle["low"] > candle["high"]
            or isinstance(timestamp_ms, bool) or not isinstance(timestamp_ms, int)
            or timestamp_ms <= trade["opened_at_ms"]
            or (trade.get("last_candle_ms") is not None
                and timestamp_ms <= trade["last_candle_ms"])):
        raise ValueError("invalid_candle")
    trade["last_candle_ms"] = timestamp_ms
    side = trade["side"]
    entry = trade["entry_price"]
    stop = trade["stop_loss"]
    trailing = trade["trailing_stop"]
    effective_stop = max(stop, trailing) if side == "LONG" and trailing is not None else stop
    if side == "SHORT" and trailing is not None:
        effective_stop = min(stop, trailing)
    stop_hit = candle["low"] <= effective_stop if side == "LONG" else candle["high"] >= effective_stop
    tp1_hit = candle["high"] >= trade["take_profit_1"] if side == "LONG" else candle["low"] <= trade["take_profit_1"]
    tp2_hit = candle["high"] >= trade["take_profit_2"] if side == "LONG" else candle["low"] <= trade["take_profit_2"]

    if stop_hit:
        exit_price = min(candle["open"], effective_stop) if side == "LONG" else max(candle["open"], effective_stop)
        quantity = trade["quantity_remaining"]
        trade["realized_gross_pnl"] += _pnl(side, entry, exit_price, quantity)
        trade["fees_paid"] += exit_price * quantity * trade["fee_rate"]
        trade["quantity_closed"] += quantity
        trade["quantity_remaining"] = 0.0
        trade["status"] = "CLOSED"
        trade["closed_at_ms"] = timestamp_ms
        trade["exit_reason"] = "STOP_LOSS" if trailing is None or effective_stop == stop else "TRAILING_STOP"
        return {"status": "CLOSED", "action": trade["exit_reason"], "exit_price": exit_price}

    actions = []
    if tp1_hit and not trade.get("tp1_taken", False) and trade["quantity_remaining"] > 0:
        quantity = min(trade["quantity_remaining"], trade["quantity_initial"] * 0.5)
        trade["realized_gross_pnl"] += _pnl(side, entry, trade["take_profit_1"], quantity)
        trade["fees_paid"] += trade["take_profit_1"] * quantity * trade["fee_rate"]
        trade["quantity_remaining"] -= quantity
        trade["quantity_closed"] += quantity
        trade["tp1_taken"] = True
        actions.append("TP1_PARTIAL")
    if tp2_hit and trade["quantity_remaining"] > 0:
        quantity = trade["quantity_remaining"]
        trade["realized_gross_pnl"] += _pnl(side, entry, trade["take_profit_2"], quantity)
        trade["fees_paid"] += trade["take_profit_2"] * quantity * trade["fee_rate"]
        trade["quantity_remaining"] = 0.0
        trade["quantity_closed"] += quantity
        trade["status"] = "CLOSED"
        trade["closed_at_ms"] = timestamp_ms
        trade["exit_reason"] = "TAKE_PROFIT_2"
        actions.append("TP2_FINAL")
    elif actions and trade["quantity_remaining"] > 0:
        # After TP1, protect the remainder at entry, without loosening a tighter stop.
        update_trailing_stop(trade, candidate_stop=entry)
    return {"status": trade["status"], "action": "+".join(actions) if actions else "HOLD"}


def trade_summary(trade, *, mark_price=None):
    """Return net realized PnL and, if open, mark-to-market net PnL."""
    remaining = trade["quantity_remaining"]
    unrealized = 0.0
    if remaining:
        if not _finite_positive(mark_price):
            raise ValueError("mark_price_required_for_open_trade")
        unrealized = _pnl(trade["side"], trade["entry_price"], float(mark_price), remaining)
    gross = trade["realized_gross_pnl"] + unrealized
    net = gross - trade["fees_paid"] - trade["funding_paid"]
    return {
        "status": trade["status"],
        "gross_pnl": gross,
        "fees_paid": trade["fees_paid"],
        "funding_paid": trade["funding_paid"],
        "net_pnl": net,
        "quantity_remaining": remaining,
        "exit_reason": trade["exit_reason"],
    }
