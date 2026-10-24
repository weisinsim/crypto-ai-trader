"""V12 hybrid historical simulator for precomputed, causally aligned feature CSVs.

Signal is computed at candle close and may enter no earlier than the next candle open.
This research simulator is deliberately conservative when stop and target touch in the
same candle: the stop is assumed to execute first. It is not an exchange execution model.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any

from research.v12_hybrid_engine import evaluate_hybrid, MODEL_VERSION

REQUIRED_CANDLE = ("t", "o", "h", "l", "c")
REQUIRED_FEATURES = (
    "ema20_1h", "ema50_1h", "ema20_4h", "ema50_4h", "rsi_1h", "atr_1h",
    "candle_closed", "data_fresh",
)
OPTIONAL_NUMERIC = (
    "ema200_1h", "ema200_4h", "atr_pct", "adx_1h", "vol_ratio", "vwap_1h",
    "support", "resistance", "funding_rate", "oi_change_pct", "momentum_pct",
    "macd_hist", "spread_bps",
)
BOOL_FIELDS = ("breakout_up", "breakout_down", "retest_long", "retest_short",
               "candle_closed", "data_fresh")


def _float(value: Any, name: str, *, required: bool = False) -> float | None:
    if value is None or value == "":
        if required:
            raise ValueError(f"missing required numeric field: {name}")
        return None
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"non-finite numeric field: {name}")
    return result


def load_feature_csv(path: str) -> list[dict]:
    """Load strict chronological OHLC + precomputed feature rows from CSV."""
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        columns = set(reader.fieldnames or [])
        missing = sorted(set(REQUIRED_CANDLE + REQUIRED_FEATURES) - columns)
        if missing:
            raise ValueError(f"feature CSV missing required columns: {missing}")
        rows = []
        previous_ts = None
        for line, raw in enumerate(reader, start=2):
            try:
                if not str(raw["t"]).isdigit():
                    raise ValueError("timestamp must be integer milliseconds")
                source_symbol = str(raw.get("symbol") or "").strip().upper()
                row = {"t": int(raw["t"]), "symbol": source_symbol or None}
                if previous_ts is not None and row["t"] <= previous_ts:
                    raise ValueError("timestamps must be strictly increasing")
                previous_ts = row["t"]
                for key in ("o", "h", "l", "c"):
                    row[key] = _float(raw.get(key), key, required=True)
                if min(row[k] for k in ("o", "h", "l", "c")) <= 0:
                    raise ValueError("OHLC prices must be positive")
                if row["h"] < max(row["o"], row["c"], row["l"]) or row["l"] > min(row["o"], row["c"]):
                    raise ValueError("invalid OHLC high/low relationship")
                for key in REQUIRED_FEATURES + OPTIONAL_NUMERIC:
                    if key in ("candle_closed", "data_fresh"):
                        value = str(raw.get(key, "")).strip().lower()
                        if value not in ("true", "false", "1", "0"):
                            raise ValueError(f"{key} must be explicitly true/false")
                        row[key] = value in ("true", "1")
                    else:
                        row[key] = _float(raw.get(key), key, required=key in REQUIRED_FEATURES)
                for key in BOOL_FIELDS:
                    if key not in row:
                        value = str(raw.get(key, "")).strip().lower()
                        row[key] = value in ("true", "1")
                row["cvd_direction"] = str(raw.get("cvd_direction") or "UNKNOWN").upper()
                row["market_regime"] = str(raw.get("market_regime") or "UNKNOWN").upper()
                rows.append(row)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f"invalid feature CSV row at line {line}: {exc}") from exc
    if len(rows) < 3:
        raise ValueError("feature CSV must contain at least three rows")
    return rows


def _features(row: dict) -> dict:
    return {key: row.get(key) for key in (
        "c", "ema20_1h", "ema50_1h", "ema200_1h", "ema20_4h", "ema50_4h",
        "ema200_4h", "rsi_1h", "atr_1h", "atr_pct", "adx_1h", "vol_ratio",
        "vwap_1h", "support", "resistance", "funding_rate", "oi_change_pct",
        "momentum_pct", "macd_hist", "breakout_up", "breakout_down",
        "retest_long", "retest_short", "cvd_direction", "market_regime",
        "data_fresh", "candle_closed", "spread_bps",
    )} | {"price": row["c"]}


def _adverse_fill(price: float, direction: int, *, entry: bool, slippage_rate: float) -> float:
    adverse_sign = direction if entry else -direction
    return price * (1 + adverse_sign * slippage_rate)


def _mark_to_market_equity_curve(rows: list[dict], trades: list[dict], initial_equity: float):
    """Rebuild equity at every held 1h candle close, including unrealized PnL."""
    curve = [{"t": rows[0]["t"], "equity": initial_equity, "kind": "initial"}]
    if not trades:
        return curve, 0.0
    index_by_ts = {row["t"]: i for i, row in enumerate(rows)}
    peak = initial_equity
    max_drawdown = 0.0
    for trade in trades:
        entry_index = index_by_ts.get(trade["entry_time"])
        exit_index = index_by_ts.get(trade["exit_time"])
        if entry_index is None or exit_index is None or exit_index < entry_index:
            raise ValueError("trade timestamps do not align to feature candle timestamps")
        direction = 1 if trade["direction"] == "LONG" else -1
        entry = trade["entry_price"]
        quantity = trade["quantity"]
        exits = trade["exits"]
        funding = trade["funding_events"]
        for index in range(entry_index, exit_index + 1):
            candle = rows[index]
            ts = candle["t"]
            completed_exits = [item for item in exits if item["t"] <= ts]
            exited_quantity = sum(item["qty"] for item in completed_exits)
            realized_gross = sum(
                (item["price"] - entry) * direction * item["qty"]
                for item in completed_exits
            )
            fees = trade["entry_fee"] + sum(item["fee"] for item in completed_exits)
            funding_pnl = sum(
                item["cashflow"] for item in funding
                if item["funding_time"] <= ts + 3_600_000
            )
            remaining = max(0.0, quantity - exited_quantity)
            mark_equity = (
                trade["equity_before"] + realized_gross
                + (candle["c"] - entry) * direction * remaining
                - fees + funding_pnl
            )
            if index == exit_index and remaining <= quantity * 1e-8:
                mark_equity = trade["equity_after"]
            peak = max(peak, mark_equity)
            drawdown = (peak - mark_equity) / peak if peak > 0 else 1.0
            max_drawdown = max(max_drawdown, drawdown)
            curve.append({
                "t": ts + 3_600_000, "equity": round(mark_equity, 10),
                "kind": "mark_to_market",
            })
    return curve, max_drawdown


def run_v12_backtest(
    rows: list[dict],
    symbol: str,
    *,
    fee_rate: float = 0.0005,
    slippage_rate: float = 0.0002,
    initial_equity: float = 1.0,
    risk_fraction: float = 0.005,
    max_leverage: float = 3.0,
    min_score_override: int | None = None,
    min_edge_override: int | None = None,
    funding_events: list[dict] | None = None,
    short_rsi_floor: float | None = None,
    trailing_atr_multiple: float = 1.5,
    max_holding_bars: int = 72,
    signal_reversal_exit: bool = True,
) -> dict:
    """Run next-open V12 simulation with partial targets, ATR trailing, reversal and time exits."""
    if len(rows) < 3:
        raise ValueError("at least three rows are required")
    source_symbols = {row.get("symbol") for row in rows if row.get("symbol")}
    if len(source_symbols) > 1:
        raise ValueError("feature CSV must not mix symbols")
    if source_symbols and source_symbols != {str(symbol).upper()}:
        raise ValueError("requested --symbol does not match feature CSV symbol")
    for name, value in (("fee_rate", fee_rate), ("slippage_rate", slippage_rate),
                        ("initial_equity", initial_equity), ("risk_fraction", risk_fraction),
                        ("max_leverage", max_leverage), ("trailing_atr_multiple", trailing_atr_multiple)):
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite")
    if fee_rate < 0 or slippage_rate < 0 or initial_equity <= 0:
        raise ValueError("cost rates must be nonnegative and initial equity positive")
    if not 0 < risk_fraction <= 0.02 or not 0 < max_leverage <= 5:
        raise ValueError("risk_fraction must be in (0, 2%] and max_leverage in (0, 5]")
    if not 0.1 <= trailing_atr_multiple <= 5.0:
        raise ValueError("trailing_atr_multiple must be in [0.1, 5]")
    if isinstance(max_holding_bars, bool) or not isinstance(max_holding_bars, int) or not 1 <= max_holding_bars <= 720:
        raise ValueError("max_holding_bars must be an integer in [1, 720]")
    if not isinstance(signal_reversal_exit, bool):
        raise ValueError("signal_reversal_exit must be boolean")
    funding_events = list(funding_events or [])
    previous_funding_ts = None
    for event in funding_events:
        if event.get("symbol") and str(event["symbol"]).upper() != str(symbol).upper():
            raise ValueError("funding event symbol does not match requested symbol")
        event_ts = event.get("funding_time")
        rate = event.get("funding_rate")
        mark_price = event.get("mark_price")
        if isinstance(event_ts, bool) or not isinstance(event_ts, int) or event_ts < 0:
            raise ValueError("funding_time must be a nonnegative integer timestamp")
        if previous_funding_ts is not None and event_ts <= previous_funding_ts:
            raise ValueError("funding events must be strictly chronological")
        if not isinstance(rate, (int, float)) or not math.isfinite(rate):
            raise ValueError("funding_rate must be finite")
        if not isinstance(mark_price, (int, float)) or not math.isfinite(mark_price) or mark_price <= 0:
            raise ValueError("funding mark_price must be finite and positive")
        previous_funding_ts = event_ts

    equity = initial_equity
    peak = equity
    max_dd = 0.0
    trades = []
    equity_curve = [{"t": rows[0]["t"], "equity": equity}]
    i = 0
    while i < len(rows) - 1:
        signal_row = rows[i]
        result = evaluate_hybrid(symbol, _features(signal_row), min_score_override=min_score_override, min_edge_override=min_edge_override, short_rsi_floor=short_rsi_floor)
        if result["signal"] not in ("LONG", "SHORT") or result.get("status") != "RESEARCH_CANDIDATE":
            i += 1
            continue

        direction = 1 if result["signal"] == "LONG" else -1
        entry_row = rows[i + 1]
        atr = signal_row.get("atr_1h")
        if atr is None or atr <= 0:
            i += 1
            continue
        entry = _adverse_fill(entry_row["o"], direction, entry=True, slippage_rate=slippage_rate)
        stop_distance = 1.5 * atr
        stop = entry - direction * stop_distance
        tp1 = entry + direction * 2.0 * stop_distance
        tp2 = entry + direction * 3.0 * stop_distance
        risk_budget = equity * risk_fraction
        qty = min(risk_budget / stop_distance, equity * max_leverage / entry)
        if qty <= 0 or not math.isfinite(qty):
            i += 1
            continue

        remaining = qty
        gross_pnl = 0.0
        entry_fee = entry * qty * fee_rate
        fees = entry_fee
        exits = []
        stop_updates = []
        outcome = "END_OF_DATA"
        active_stop = stop
        tp1_done = False
        high_water = entry
        low_water = entry
        bars_held = 0
        pending_reversal = False
        j = i + 1
        while j < len(rows):
            bar = rows[j]

            # Reversal is known at the previous candle close and executes at this open.
            if pending_reversal:
                fill = _adverse_fill(bar["o"], direction, entry=False, slippage_rate=slippage_rate)
                gross_pnl += (fill - entry) * direction * remaining
                exit_fee = fill * remaining * fee_rate
                fees += exit_fee
                exits.append({"t": bar["t"], "kind": "SIGNAL_REVERSAL_EXIT", "price": fill, "qty": remaining, "fee": exit_fee})
                remaining = 0.0
                outcome = "TP1+SIGNAL_REVERSAL_EXIT" if tp1_done else "SIGNAL_REVERSAL_EXIT"
                break

            # Hard time stop prevents an intended short-term trade becoming a long hold.
            if bars_held >= max_holding_bars:
                fill = _adverse_fill(bar["o"], direction, entry=False, slippage_rate=slippage_rate)
                gross_pnl += (fill - entry) * direction * remaining
                exit_fee = fill * remaining * fee_rate
                fees += exit_fee
                exits.append({"t": bar["t"], "kind": "TIME_EXIT", "price": fill, "qty": remaining, "fee": exit_fee})
                remaining = 0.0
                outcome = "TP1+TIME_EXIT" if tp1_done else "TIME_EXIT"
                break

            stop_hit = bar["l"] <= active_stop if direction == 1 else bar["h"] >= active_stop
            tp1_hit = bar["h"] >= tp1 if direction == 1 else bar["l"] <= tp1
            tp2_hit = bar["h"] >= tp2 if direction == 1 else bar["l"] <= tp2

            # Conservative OHLC ordering: active stop wins if stop and any target touch
            # on the same bar. For a gap through the stop, use the worse open.
            if stop_hit:
                raw_exit = min(active_stop, bar["o"]) if direction == 1 else max(active_stop, bar["o"])
                fill = _adverse_fill(raw_exit, direction, entry=False, slippage_rate=slippage_rate)
                gross_pnl += (fill - entry) * direction * remaining
                exit_fee = fill * remaining * fee_rate
                fees += exit_fee
                exit_kind = "TRAILING_STOP" if tp1_done else "STOP"
                exits.append({"t": bar["t"], "kind": exit_kind, "price": fill, "qty": remaining, "fee": exit_fee})
                remaining = 0.0
                outcome = "TP1+TRAILING_STOP" if tp1_done else "STOP"
                break

            if tp1_hit and not tp1_done:
                take_qty = min(qty * 0.5, remaining)
                fill = _adverse_fill(tp1, direction, entry=False, slippage_rate=slippage_rate)
                gross_pnl += (fill - entry) * direction * take_qty
                exit_fee = fill * take_qty * fee_rate
                fees += exit_fee
                remaining -= take_qty
                tp1_done = True
                exits.append({"t": bar["t"], "kind": "TP1", "price": fill, "qty": take_qty, "fee": exit_fee})

            if tp2_hit and remaining > 0:
                fill = _adverse_fill(tp2, direction, entry=False, slippage_rate=slippage_rate)
                exit_fee = fill * remaining * fee_rate
                gross_pnl += (fill - entry) * direction * remaining
                fees += exit_fee
                exits.append({"t": bar["t"], "kind": "TP2", "price": fill, "qty": remaining, "fee": exit_fee})
                remaining = 0.0
                outcome = "TP1+TP2" if tp1_done else "TP2"
                break

            # Update trailing stop only after this candle has completed; it is active
            # from the next candle, avoiding intrabar lookahead.
            high_water = max(high_water, bar["h"])
            low_water = min(low_water, bar["l"])
            bars_held += 1
            if tp1_done and remaining > 0:
                current_atr = bar.get("atr_1h") or atr
                previous_stop = active_stop
                if direction == 1:
                    active_stop = max(active_stop, high_water - trailing_atr_multiple * current_atr)
                else:
                    active_stop = min(active_stop, low_water + trailing_atr_multiple * current_atr)
                if active_stop != previous_stop:
                    stop_updates.append({"t": bar["t"], "from": previous_stop, "to": active_stop})

            if signal_reversal_exit and remaining > 0:
                close_signal = evaluate_hybrid(
                    symbol, _features(bar), min_score_override=min_score_override,
                    min_edge_override=min_edge_override, short_rsi_floor=short_rsi_floor,
                )
                opposite = "SHORT" if direction == 1 else "LONG"
                if close_signal["signal"] == opposite and close_signal.get("status") == "RESEARCH_CANDIDATE":
                    pending_reversal = True

            j += 1

        if remaining > 0:
            bar = rows[-1]
            fill = _adverse_fill(bar["c"], direction, entry=False, slippage_rate=slippage_rate)
            gross_pnl += (fill - entry) * direction * remaining
            exit_fee = fill * remaining * fee_rate
            fees += exit_fee
            exits.append({"t": bar["t"], "kind": "END_OF_DATA", "price": fill, "qty": remaining, "fee": exit_fee})
            remaining = 0.0
            outcome = "TP1+END_OF_DATA" if tp1_done else "END_OF_DATA"

        funding_pnl = 0.0
        funding_applied = []
        exit_time = exits[-1]["t"]
        for event in funding_events:
            event_ts = event["funding_time"]
            # Settlement exactly at entry is excluded as a boundary ambiguity.
            # Settlement at an exit candle's open is applied before that candle's
            # OHLC exit, because the position existed at the settlement instant.
            if not (entry_row["t"] < event_ts <= exit_time):
                continue
            exited_before_event = sum(exit_row["qty"] for exit_row in exits if exit_row["t"] < event_ts)
            quantity_at_event = max(0.0, qty - exited_before_event)
            if quantity_at_event <= 0:
                continue
            cashflow = -direction * quantity_at_event * event["mark_price"] * event["funding_rate"]
            funding_pnl += cashflow
            funding_applied.append({
                "funding_time": event_ts, "funding_rate": event["funding_rate"],
                "mark_price": event["mark_price"], "quantity": quantity_at_event,
                "cashflow": cashflow,
            })

        net_pnl = gross_pnl - fees + funding_pnl
        equity_before = equity
        equity += net_pnl
        if equity <= 0:
            equity = 0.0
        peak = max(peak, equity)
        drawdown = (peak - equity) / peak if peak > 0 else 1.0
        max_dd = max(max_dd, drawdown)
        trades.append({
            "symbol": symbol.upper(), "direction": result["signal"],
            "signal_time": signal_row["t"], "entry_time": entry_row["t"],
            "entry_price": entry, "quantity": qty, "entry_fee": entry_fee,
            "stop": stop, "final_stop": active_stop, "stop_updates": stop_updates,
            "tp1": tp1, "tp2": tp2, "holding_bars": min(j, len(rows) - 1) - (i + 1) + 1,
            "signal_score": result["score"], "gross_pnl": gross_pnl, "fees": fees,
            "funding_pnl": funding_pnl, "funding_events": funding_applied,
            "net_pnl": net_pnl, "equity_before": equity_before, "equity_after": equity,
            "return_on_equity_pct": net_pnl / equity_before * 100 if equity_before else None,
            "exit_time": exits[-1]["t"], "exit_kind": outcome, "exits": exits,
        })
        equity_curve.append({"t": exits[-1]["t"], "equity": equity})
        i = max(j + 1, i + 1)
        if equity <= 0:
            break

    # Drawdown must include open-position adverse excursions, not just closed-trade
    # equity. Rebuild a candle-close mark-to-market curve from each trade ledger.
    equity_curve, max_dd = _mark_to_market_equity_curve(rows, trades, initial_equity)
    wins = [trade for trade in trades if trade["net_pnl"] > 0]
    losses = [trade for trade in trades if trade["net_pnl"] < 0]
    gross_profit = sum(t["net_pnl"] for t in wins)
    gross_loss = -sum(t["net_pnl"] for t in losses)
    return {
        "model_version": MODEL_VERSION, "symbol": symbol.upper(),
        "status": "RESEARCH_ONLY", "profitability_claim_eligible": False,
        "strategy_approval": False, "live_trading_enabled": False,
        "bars": len(rows), "trades": len(trades),
        "wins": len(wins), "losses": len(losses),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 4) if trades else None,
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else (None if not gross_profit else "INF"),
        "funding_pnl_total": round(sum(t["funding_pnl"] for t in trades), 8),
        "net_return_pct": round((equity / initial_equity - 1) * 100, 4),
        "max_drawdown_pct": round(max_dd * 100, 4),
        "equity_final": round(equity, 8),
        "assumptions": {"fee_rate_per_side": fee_rate, "slippage_rate_per_side": slippage_rate,
                        "risk_fraction": risk_fraction, "max_leverage": max_leverage,
                        "entry": "next candle open", "same_bar_stop_target": "stop first",
                        "partial_targets": "50% at 2R, remaining 50% at 3R",
                        "trailing_atr_multiple": trailing_atr_multiple,
                        "max_holding_bars": max_holding_bars,
                        "signal_reversal_exit": signal_reversal_exit,
                        "funding_costs_included": bool(funding_events),
                        "funding_boundary_policy": "exclude event at entry timestamp; include event at exit candle open before OHLC exit"},
        "trade_log": trades, "equity_curve": equity_curve,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, help="OHLC + causally precomputed V12 feature CSV")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--fee-rate", type=float, default=0.0005, help="per-side fee fraction")
    parser.add_argument("--slippage-rate", type=float, default=0.0002, help="per-side slippage fraction")
    parser.add_argument("--initial-equity", type=float, default=1.0)
    parser.add_argument("--risk-fraction", type=float, default=0.005)
    parser.add_argument("--max-leverage", type=float, default=3.0)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    input_path = Path(args.csv).resolve()
    output_path = Path(args.output).resolve()
    if input_path == output_path:
        raise ValueError("output path must not overwrite input CSV")
    rows = load_feature_csv(str(input_path))
    report = run_v12_backtest(
        rows, args.symbol, fee_rate=args.fee_rate, slippage_rate=args.slippage_rate,
        initial_equity=args.initial_equity, risk_fraction=args.risk_fraction,
        max_leverage=args.max_leverage,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
