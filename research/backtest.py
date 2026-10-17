"""Conservative OHLCV backtest primitives; research use only, not a live signal engine."""
from __future__ import annotations

from math import isfinite
from typing import Iterable


REQUIRED = ("t", "o", "h", "l", "c")


def validate_candles(rows: Iterable[dict], interval_ms: int | None = None) -> list[dict]:
    """Validate chronological OHLCV rows and optionally require contiguous timestamps."""
    if interval_ms is not None and (
        isinstance(interval_ms, bool) or not isinstance(interval_ms, int) or interval_ms <= 0
    ):
        raise ValueError("interval_ms must be a positive integer number of milliseconds")
    data = list(rows)
    previous = None
    for i, row in enumerate(data):
        if not isinstance(row, dict):
            raise ValueError(f"row {i} must be a mapping")
        missing = [key for key in REQUIRED if key not in row]
        if missing:
            raise ValueError(f"row {i} missing fields: {missing}")
        raw_ts = row["t"]
        if isinstance(raw_ts, bool):
            raise ValueError(f"row {i} timestamp must be an integer millisecond value")
        if isinstance(raw_ts, int):
            ts = raw_ts
        elif isinstance(raw_ts, float) and isfinite(raw_ts) and raw_ts.is_integer():
            ts = int(raw_ts)
        elif isinstance(raw_ts, str) and raw_ts.strip().isdigit():
            ts = int(raw_ts.strip())
        else:
            raise ValueError(f"row {i} timestamp must be an integer millisecond value")
        if ts < 0:
            raise ValueError(f"row {i} timestamp cannot be negative")
        vals = {}
        for field in ("o", "h", "l", "c"):
            raw_value = row[field]
            if isinstance(raw_value, bool):
                raise ValueError(f"row {i} has invalid OHLC values")
            try:
                vals[field] = float(raw_value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"row {i} has invalid OHLC values") from exc
        for field in ("v", "q"):
            if field in row:
                if isinstance(row[field], bool):
                    raise ValueError(f"row {i} has invalid {field}")
                try:
                    metric = float(row[field])
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"row {i} has invalid {field}") from exc
                if not isfinite(metric) or metric < 0:
                    raise ValueError(f"row {i} has invalid {field}")
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
    funding_entry_boundary_policy: str = "exclude",
    allow_short: bool = True,
    risk_fraction: float = 0.01,
    trade_start_ts: int | None = None,
) -> dict:
    """Next-open EMA cross baseline with fixed-risk exits and conservative same-bar ordering.

    Signals are computed from completed bar i-1 and filled at bar i open.
    Optional historical funding events are settled only when an existing position
    is open at an event timestamp by default; event rows require funding_time/rate/mark_price.
    funding_entry_boundary_policy="include" is a sensitivity assumption for entry-time
    boundary events, not a claim about exchange eligibility.
    To avoid silently mis-timing fees, every event must align with a candle open.
    If a bar touches both stop and target, stop is assumed first. Fees are
    charged on entry and exit notional; funding is charged per held bar.
    Returns percentage-equity results with one unit of initial equity and no leverage.
    """
    if funding_entry_boundary_policy not in ("exclude", "include"):
        raise ValueError("funding_entry_boundary_policy must be 'exclude' or 'include'")
    rows = validate_candles(candles)
    funding_events = {}
    if funding_rates is not None:
        funding_rates = list(funding_rates)
        if not rows and funding_rates:
            raise ValueError("cannot apply historical funding events without candle data")
        candle_timestamps = sorted(int(row["t"]) for row in rows)
        candle_timestamp_set = set(candle_timestamps)
        previous_funding_ts = None
        parsed_events = []
        for index, event in enumerate(funding_rates):
            if not isinstance(event, dict):
                raise ValueError(f"funding event {index} must be a mapping")
            if "funding_time" not in event or "funding_rate" not in event or "mark_price" not in event:
                raise ValueError(
                    f"funding event {index} requires funding_time, funding_rate, and mark_price"
                )
            raw_ts = event["funding_time"]
            if isinstance(raw_ts, bool):
                raise ValueError(f"funding event {index} timestamp must be an integer millisecond value")
            if isinstance(raw_ts, int):
                raw_funding_ts = raw_ts
            elif isinstance(raw_ts, float) and isfinite(raw_ts) and raw_ts.is_integer():
                raw_funding_ts = int(raw_ts)
            elif isinstance(raw_ts, str) and raw_ts.strip().isdigit():
                raw_funding_ts = int(raw_ts.strip())
            else:
                raise ValueError(f"funding event {index} timestamp must be an integer millisecond value")
            if raw_funding_ts < 0:
                raise ValueError(f"funding event {index} timestamp cannot be negative")
            try:
                rate = float(event["funding_rate"])
                mark_price = float(event["mark_price"])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"funding event {index} has invalid numeric fields") from exc
            if isinstance(event["funding_rate"], bool) or isinstance(event["mark_price"], bool):
                raise ValueError(f"funding event {index} has invalid numeric fields")
            parsed_events.append((raw_funding_ts, rate, mark_price))
        for raw_funding_ts, rate, mark_price in sorted(parsed_events, key=lambda item: item[0]):
            if not isfinite(rate) or not isfinite(mark_price) or mark_price <= 0:
                raise ValueError("funding events require finite rates and positive finite mark prices")
            if previous_funding_ts == raw_funding_ts:
                raise ValueError(f"duplicate funding timestamp: {raw_funding_ts}")
            # Never snap a funding event to a nearby candle. Even a small
            # timestamp offset makes settlement eligibility ambiguous in OHLC data.
            # Use exact event-aware execution data or reject this backtest input.
            if raw_funding_ts not in candle_timestamp_set:
                raise ValueError(
                    f"funding timestamp {raw_funding_ts} does not exactly match a candle open; "
                    "approximate timestamp normalization is disabled"
                )
            funding_ts = raw_funding_ts
            if funding_ts in funding_events:
                raise ValueError(f"funding events normalize to duplicate candle timestamp: {funding_ts}")
            funding_events[funding_ts] = {"funding_rate": rate, "mark_price": mark_price}
            previous_funding_ts = raw_funding_ts
    integer_params = {
        "fast": fast, "slow": slow, "trend_period": trend_period, "atr_period": atr_period,
    }
    for name, value in integer_params.items():
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{name} must be an integer")
    if fast < 2 or slow <= fast or atr_period < 1 or trend_period < 0:
        raise ValueError("require 2 <= fast < slow, atr_period >= 1 and trend_period >= 0")
    if trade_start_ts is not None and (
        isinstance(trade_start_ts, bool) or not isinstance(trade_start_ts, int) or trade_start_ts < 0
    ):
        raise ValueError("trade_start_ts must be a non-negative integer millisecond timestamp")
    numeric_params = {
        "stop_atr": stop_atr, "target_atr": target_atr,
        "fee_rate": fee_rate, "slippage_rate": slippage_rate,
        "funding_rate_per_bar": funding_rate_per_bar,
        "risk_fraction": risk_fraction,
    }
    for name, value in numeric_params.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
            raise ValueError(f"{name} must be a finite number")
    if stop_atr <= 0 or target_atr <= 0:
        raise ValueError("stop_atr and target_atr must be positive")
    if fee_rate < 0 or fee_rate >= 1:
        raise ValueError("fee_rate must be in [0, 1)")
    if slippage_rate < 0 or slippage_rate >= 1:
        raise ValueError("slippage_rate must be in [0, 1)")
    if not 0 < risk_fraction <= 1:
        raise ValueError("risk_fraction must be in (0, 1]")
    if len(rows) < slow + 2:
        return {"status": "INSUFFICIENT_DATA", "bars": len(rows), "trades": 0,
                "net_return_pct": 0.0, "max_drawdown_pct": 0.0, "win_rate_pct": None,
                "profit_factor": None, "expectancy_pct": None, "equity_floor_hit": False, "trade_log": []}

    closes = [float(r["c"]) for r in rows]
    fast_ema, slow_ema = _ema(closes, fast), _ema(closes, slow)
    trend_ema = _ema(closes, trend_period) if trend_period >= 2 else None
    if trend_period == 1:
        raise ValueError("trend_period must be 0 (disabled) or >= 2")
    atrs = _atr(rows, atr_period)
    indicator_series = [fast_ema, slow_ema, atrs]
    if trend_ema is not None:
        indicator_series.append(trend_ema)
    if any(
        value is not None and not isfinite(value)
        for series in indicator_series
        for value in series
    ):
        raise ValueError("indicator calculation produced a non-finite value")
    equity = 1.0
    equity_floor = 1e-6
    equity_floor_hit = False
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
        if not all(isfinite(value) for value in (
            exit_fill, gross_pnl, exit_fee, funding_cost, pnl, new_equity,
        )):
            raise ValueError("trade accounting produced a non-finite value")
        funding_cost_total = (pos.get("funding_cost", 0.0) if funding_rates is not None else
                              pos["qty"] * pos["entry_fill"] * funding_rate_per_bar * side * pos["bars_held"])
        funding_cashflow = -funding_cost_total
        settlement_timestamps = pos.get("funding_settlement_timestamps", [])
        gap_stop_at_open = reason == "STOP" and (
            (side == 1 and float(bar["o"]) < pos["stop"]) or
            (side == -1 and float(bar["o"]) > pos["stop"])
        )
        # Ambiguity also exists when a position opens on the exact funding
        # timestamp: OHLC candle timestamps alone cannot prove whether the
        # entry was before or after the exchange settlement instant.
        funding_entry_boundary_ambiguous = bool(
            funding_rates is not None and int(pos["entry_time"]) in funding_events
        )
        funding_boundary_ambiguous = bool(
            funding_rates is not None and (
                funding_entry_boundary_ambiguous or
                (gap_stop_at_open and int(bar["t"]) in settlement_timestamps)
            )
        )
        equity_before = pos["equity_before"]
        if not isfinite(equity_before) or equity_before <= 0:
            raise ValueError("trade return calculation requires positive finite entry equity")
        return_pct = (new_equity / equity_before - 1) * 100
        if not isfinite(return_pct):
            raise ValueError("trade return calculation produced a non-finite value")
        trade = {
            "side": "LONG" if side == 1 else "SHORT",
            "entry_time": pos["entry_time"], "exit_time": int(bar["t"]),
            "entry": round(pos["entry_fill"], 8), "exit": round(exit_fill, 8),
            "reason": reason, "bars_held": pos["bars_held"],
            "stop": round(pos["stop"], 8), "target": round(pos["target"], 8),
            "funding_cashflow": round(funding_cashflow, 8),
            "funding_settlement_count": len(settlement_timestamps),
            "funding_boundary_ambiguous": funding_boundary_ambiguous,
            "return_pct": round(return_pct, 6),
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
            settlement_cost = (
                position["qty"] * event["mark_price"] * event["funding_rate"] * position["side"]
            )
            updated_funding_cost = position.get("funding_cost", 0.0) + settlement_cost
            if not isfinite(settlement_cost) or not isfinite(updated_funding_cost):
                raise ValueError("funding settlement produced a non-finite cost")
            position["funding_cost"] = updated_funding_cost
            position.setdefault("funding_settlement_timestamps", []).append(int(bar["t"]))
        # Measure the adverse intrabar mark BEFORE resolving this candle's
        # stop/target. Otherwise a target exit could realize a gain first and
        # hide an adverse wick that occurred while the position was still open.
        if position is not None:
            side = int(position["side"])
            adverse_price = (min(float(bar["c"]), float(bar["l"])) if side > 0
                             else max(float(bar["c"]), float(bar["h"])))
            unrealized_pnl = position["qty"] * side * (adverse_price - position["entry_fill"])
            estimated_exit_fee = abs(position["qty"] * adverse_price) * fee_rate
            accrued_funding = (position.get("funding_cost", 0.0) if funding_rates is not None else
                               position["qty"] * position["entry_fill"] * funding_rate_per_bar
                               * side * (position["bars_held"] + 1))
            adverse_equity = (equity + unrealized_pnl - position["entry_fee"]
                              - estimated_exit_fee - accrued_funding)
            if not all(isfinite(value) for value in (
                unrealized_pnl, estimated_exit_fee, accrued_funding, adverse_equity,
            )):
                raise ValueError("equity marking produced a non-finite value")
            max_dd = max(max_dd, (peak - adverse_equity) / peak if peak else 0.0)

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
                # A favorable opening gap through the target should not be
                # priced worse than the observed open in this OHLC approximation.
                if position["side"] == 1 and float(bar["o"]) > position["target"]:
                    exit_price = float(bar["o"])
                elif position["side"] == -1 and float(bar["o"]) < position["target"]:
                    exit_price = float(bar["o"])
                else:
                    exit_price = position["target"]
                reason = "TARGET"
            elif i == len(rows) - 1:
                exit_price, reason = float(bar["c"]), "END_OF_DATA"
            if exit_price is not None:
                equity, trade = finalize_position(position, bar, exit_price, reason, equity)
                trades.append(trade)
                position = None
                if equity <= equity_floor:
                    equity_floor_hit = True
                    equity = equity_floor

        # Signal is known only after the previous bar closes; fill at current open.
        if (not equity_floor_hit and can_enter_at_open and position is None and (trade_start_ts is None or int(bar["t"]) >= trade_start_ts)
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
                stop_price = open_price - side * risk
                target_price = open_price + side * risk * target_atr
                derived_values = (
                    risk, entry_fill, qty_by_risk, qty_by_notional, qty,
                    stop_price, target_price,
                )
                if not all(isfinite(value) for value in derived_values) or qty <= 0:
                    raise ValueError("position sizing produced non-finite or non-positive values")
                position = {"side": side, "entry_fill": entry_fill,
                            "stop": stop_price,
                            "target": target_price,
                            "entry_time": int(bar["t"]), "bars_held": 0,
                            "equity_before": equity, "qty": qty,
                            "entry_fee": abs(qty * entry_fill) * fee_rate,
                            "funding_cost": 0.0, "funding_settlement_timestamps": []}
                # Boundary sensitivity only: optionally model a position as
                # eligible when its entry candle timestamp equals settlement.
                # This is not an exchange fact; compare both policies.
                if (funding_rates is not None and funding_entry_boundary_policy == "include"
                        and int(bar["t"]) in funding_events):
                    event = funding_events[int(bar["t"])]
                    settlement_cost = (
                        position["qty"] * event["mark_price"] * event["funding_rate"] * position["side"]
                    )
                    updated_funding_cost = position["funding_cost"] + settlement_cost
                    if not isfinite(settlement_cost) or not isfinite(updated_funding_cost):
                        raise ValueError("funding settlement produced a non-finite cost")
                    position["funding_cost"] = updated_funding_cost
                    position["funding_settlement_timestamps"].append(int(bar["t"]))
                opened_this_bar = True

        # A newly opened position also has intrabar exposure. Mark its
        # adverse OHLC extreme before resolving a same-candle target/stop;
        # otherwise a profitable target can hide the entry candle's drawdown.
        if opened_this_bar and position is not None:
            side = int(position["side"])
            adverse_price = (min(float(bar["c"]), float(bar["l"])) if side > 0
                             else max(float(bar["c"]), float(bar["h"])))
            unrealized_pnl = position["qty"] * side * (adverse_price - position["entry_fill"])
            estimated_exit_fee = abs(position["qty"] * adverse_price) * fee_rate
            entry_bar_funding = (position.get("funding_cost", 0.0)
                                  if funding_rates is not None else 0.0)
            adverse_equity = (equity + unrealized_pnl - position["entry_fee"]
                              - estimated_exit_fee - entry_bar_funding)
            max_dd = max(max_dd, (peak - adverse_equity) / peak if peak else 0.0)

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
                if equity <= equity_floor:
                    equity_floor_hit = True
                    equity = equity_floor

        # Keep the equity curve close-marked, but measure drawdown against the
        # adverse intrabar OHLC mark as well. OHLC cannot reveal the true path;
        # this deliberately conservative estimate avoids understating risk
        # when a candle closes far from its adverse extreme.
        marked_equity = equity
        adverse_marked_equity = equity
        if position is not None:
            side = int(position["side"])
            close_price = float(bar["c"])
            adverse_price = min(close_price, float(bar["l"])) if side > 0 else max(close_price, float(bar["h"]))

            def mark_open_position(mark_price: float) -> float:
                unrealized_pnl = position["qty"] * side * (mark_price - position["entry_fill"])
                estimated_exit_fee = abs(position["qty"] * mark_price) * fee_rate
                accrued_funding = (position.get("funding_cost", 0.0) if funding_rates is not None else
                                   position["qty"] * position["entry_fill"] * funding_rate_per_bar
                                   * side * position["bars_held"])
                marked = equity + unrealized_pnl - position["entry_fee"] - estimated_exit_fee - accrued_funding
                if not all(isfinite(value) for value in (
                    unrealized_pnl, estimated_exit_fee, accrued_funding, marked,
                )):
                    raise ValueError("equity marking produced a non-finite value")
                return marked

            marked_equity = mark_open_position(close_price)
            adverse_marked_equity = mark_open_position(adverse_price)
        peak = max(peak, marked_equity)
        # Use the close-equity high-water mark and intrabar adverse trough.
        max_dd = max(max_dd, (peak - adverse_marked_equity) / peak if peak else 0)
        if equity_floor_hit:
            marked_equity = equity_floor
            adverse_marked_equity = equity_floor
        equity_curve.append({"t": int(bar["t"]), "equity": marked_equity})
        if equity_floor_hit:
            break

    if position is not None:
        bar = rows[-1]
        equity, trade = finalize_position(position, bar, float(bar["c"]), "END_OF_DATA", equity)
        trades.append(trade)
        if equity <= equity_floor:
            equity_floor_hit = True
            equity = equity_floor
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
            "equity_final": round(equity, 8), "equity_floor_hit": equity_floor_hit,
            "equity_curve": equity_curve, "trade_log": trades,
            "funding_boundary_ambiguous_trades": sum(
                1 for trade in trades if trade["funding_boundary_ambiguous"]
            )}
