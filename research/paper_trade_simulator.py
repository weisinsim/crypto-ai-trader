"""Explicit-assumption paper trade lifecycle simulator; never submits orders.

This is a research approximation, not an exchange fill model. It enters at the
next candle open, applies adverse entry and exit slippage plus fees, processes stop before target
when one OHLC bar touches both, takes 50% off at TP1, and closes the remainder
at TP2, stop, or the holding limit. Funding is a configurable adverse cost per
8-hour holding interval because historical funding is not ingested here.
"""
from __future__ import annotations

import math


def simulate_tactical_candidate(*, observation, future_candles,
                                fee_bps_per_side=5.0,
                                slippage_bps_per_side=2.0,
                                funding_bps_per_8h=0.0,
                                max_holding_candles=24):
    """Simulate one frozen LONG/SHORT candidate against candles after its signal.

    Net return is net PnL divided by slipped entry notional for unit quantity.
    Costs are modeled assumptions; results are explicitly marked unverified.
    """
    if not isinstance(observation, dict):
        raise ValueError("observation must be a mapping")
    side = observation.get("signal")
    if side not in ("LONG", "SHORT"):
        return {
            "status": "NO_TRADE", "symbol": observation.get("symbol"),
            "research_only": True, "auto_trade_authorized": False,
            "reason": "signal is not LONG or SHORT",
        }
    for name, value in (
        ("fee_bps_per_side", fee_bps_per_side),
        ("slippage_bps_per_side", slippage_bps_per_side),
        ("funding_bps_per_8h", funding_bps_per_8h),
    ):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError(f"{name} must be finite and nonnegative")
    if isinstance(max_holding_candles, bool) or not isinstance(max_holding_candles, int) or max_holding_candles < 1:
        raise ValueError("max_holding_candles must be a positive integer")
    if not isinstance(future_candles, (list, tuple)) or not future_candles:
        raise ValueError("future_candles must contain candles after the signal")
    signal_close = observation.get("signal_candle_close_ms")
    if isinstance(signal_close, bool) or not isinstance(signal_close, (int, float)) or not math.isfinite(signal_close):
        raise ValueError("observation requires a valid signal close timestamp")
    levels = {name: observation.get(name) for name in ("entry", "sl", "tp1", "tp2")}
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0 for v in levels.values()):
        raise ValueError("candidate must contain positive finite entry, stop, TP1, and TP2")
    entry_ref, stop, tp1, tp2 = levels["entry"], levels["sl"], levels["tp1"], levels["tp2"]
    if side == "LONG" and not stop < entry_ref < tp1 <= tp2:
        raise ValueError("LONG risk levels must satisfy stop < entry < TP1 <= TP2")
    if side == "SHORT" and not tp2 <= tp1 < entry_ref < stop:
        raise ValueError("SHORT risk levels must satisfy TP2 <= TP1 < entry < stop")

    valid = []
    for index, candle in enumerate(future_candles):
        if not isinstance(candle, dict):
            raise ValueError(f"future candle {index} must be a mapping")
        timestamp = candle.get("t")
        if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp <= signal_close:
            raise ValueError(f"future candle {index} timestamp must be after signal close")
        values = [candle.get(key) for key in ("o", "h", "l", "c")]
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0 for v in values):
            raise ValueError(f"future candle {index} has invalid OHLC values")
        o, h, l, c = values
        if h < max(o, c, l) or l > min(o, c, h):
            raise ValueError(f"future candle {index} has invalid OHLC geometry")
        if valid and timestamp <= valid[-1]["t"]:
            raise ValueError("future candle timestamps must be strictly increasing")
        valid.append({"t": timestamp, "o": float(o), "h": float(h), "l": float(l), "c": float(c)})

    first = valid[0]
    slip = slippage_bps_per_side / 10_000
    entry = first["o"] * (1 + slip if side == "LONG" else 1 - slip)
    if entry <= 0:
        raise ValueError("slippage-adjusted entry must be positive")
    direction = 1 if side == "LONG" else -1
    # Exit orders are assumed to fill adversely: sell exits lower for longs,
    # buy-to-cover exits higher for shorts.
    def adverse_exit_price(reference_price):
        return reference_price * (1 - slip if side == "LONG" else 1 + slip)

    quantity_remaining = 1.0
    realized_gross = 0.0
    exits = []
    tp1_done = False
    holding_limit = min(max_holding_candles, len(valid))
    reached_holding_limit = len(valid) >= max_holding_candles
    exit_reason = "MAX_HOLD" if reached_holding_limit else "DATA_END"
    exit_price = valid[holding_limit - 1]["c"]
    bars_used = holding_limit
    for index, candle in enumerate(valid[:holding_limit]):
        stop_hit = candle["l"] <= stop if side == "LONG" else candle["h"] >= stop
        tp1_hit = candle["h"] >= tp1 if side == "LONG" else candle["l"] <= tp1
        tp2_hit = candle["h"] >= tp2 if side == "LONG" else candle["l"] <= tp2
        # Ambiguous OHLC bars are resolved conservatively: stop takes priority.
        if stop_hit:
            fill = adverse_exit_price(stop)
            realized_gross += quantity_remaining * direction * (fill - entry)
            exits.append({"reason": "STOP", "price": fill, "reference_price": stop, "quantity": quantity_remaining, "bar_index": index})
            quantity_remaining = 0.0
            exit_reason, exit_price, bars_used = "STOP", stop, index + 1
            break
        if not tp1_done and tp1_hit:
            take = min(0.5, quantity_remaining)
            fill = adverse_exit_price(tp1)
            realized_gross += take * direction * (fill - entry)
            quantity_remaining -= take
            tp1_done = True
            exits.append({"reason": "TP1", "price": fill, "reference_price": tp1, "quantity": take, "bar_index": index})
        if tp2_hit and tp1_done and quantity_remaining > 0:
            fill = adverse_exit_price(tp2)
            realized_gross += quantity_remaining * direction * (fill - entry)
            exits.append({"reason": "TP2", "price": fill, "reference_price": tp2, "quantity": quantity_remaining, "bar_index": index})
            quantity_remaining = 0.0
            exit_reason, exit_price, bars_used = "TP2", tp2, index + 1
            break
        if index == holding_limit - 1:
            fill = adverse_exit_price(candle["c"])
            realized_gross += quantity_remaining * direction * (fill - entry)
            reason = "MAX_HOLD" if reached_holding_limit else "DATA_END"
            exits.append({"reason": reason, "price": fill, "reference_price": candle["c"], "quantity": quantity_remaining, "bar_index": index})
            quantity_remaining = 0.0
            exit_reason, exit_price, bars_used = reason, candle["c"], index + 1

    traded_notional = entry
    # Entry fee plus exit fees on each executed unit fraction's exit notional.
    exit_fee_notional = sum(item["quantity"] * item["price"] for item in exits)
    fee_cost = (fee_bps_per_side / 10_000) * (traded_notional + exit_fee_notional)
    # Conservative fixed funding debit per elapsed 8-hour interval, applied to entry notional.
    funding_intervals = max(0, math.ceil((bars_used - 1) / 8))
    funding_cost = traded_notional * (funding_bps_per_8h / 10_000) * funding_intervals
    net_pnl = realized_gross - fee_cost - funding_cost
    return {
        "status": "SIMULATED" if exit_reason != "DATA_END" else "CENSORED",
        "outcome_complete": exit_reason != "DATA_END",
        "symbol": observation.get("symbol"),
        "side": side,
        "signal_candle_close_ms": signal_close,
        "entry_timestamp_ms": first["t"],
        "entry_reference": entry_ref,
        "entry_price_assumption": entry,
        "stop_loss": stop, "take_profit_1": tp1, "take_profit_2": tp2,
        "exit_reason": exit_reason, "exit_price_assumption": exit_price,
        "bars_held": bars_used, "exits": exits,
        "gross_pnl_per_unit": realized_gross,
        "fee_cost_assumption": fee_cost,
        "funding_cost_assumption": funding_cost,
        "funding_intervals": funding_intervals,
        "net_pnl_per_unit": net_pnl,
        "net_return_on_entry_notional": net_pnl / traded_notional,
        "costs_verified": False,
        "fill_prices_verified": False,
        "research_only": True,
        "auto_trade_authorized": False,
    }
