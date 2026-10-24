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
                row = {"t": int(raw["t"])}
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


def run_v12_backtest(
    rows: list[dict],
    symbol: str,
    *,
    fee_rate: float = 0.0005,
    slippage_rate: float = 0.0002,
    initial_equity: float = 1.0,
    risk_fraction: float = 0.005,
    max_leverage: float = 3.0,
) -> dict:
    """Run a single-position, next-open simulator with 50/50 TP1/TP2 exits."""
    if len(rows) < 3:
        raise ValueError("at least three rows are required")
    for name, value in (("fee_rate", fee_rate), ("slippage_rate", slippage_rate),
                        ("initial_equity", initial_equity), ("risk_fraction", risk_fraction),
                        ("max_leverage", max_leverage)):
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite")
    if fee_rate < 0 or slippage_rate < 0 or initial_equity <= 0:
        raise ValueError("cost rates must be nonnegative and initial equity positive")
    if not 0 < risk_fraction <= 0.02 or not 0 < max_leverage <= 5:
        raise ValueError("risk_fraction must be in (0, 2%] and max_leverage in (0, 5]")

    equity = initial_equity
    peak = equity
    max_dd = 0.0
    trades = []
    equity_curve = [{"t": rows[0]["t"], "equity": equity}]
    i = 0
    while i < len(rows) - 1:
        signal_row = rows[i]
        result = evaluate_hybrid(symbol, _features(signal_row))
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
        fees = entry * qty * fee_rate
        exits = []
        outcome = "END_OF_DATA"
        j = i + 1
        while j < len(rows):
            bar = rows[j]
            stop_hit = bar["l"] <= stop if direction == 1 else bar["h"] >= stop
            tp1_hit = bar["h"] >= tp1 if direction == 1 else bar["l"] <= tp1
            tp2_hit = bar["h"] >= tp2 if direction == 1 else bar["l"] <= tp2

            # Conservative OHLC ordering: stop wins if stop and any target touch
            # on the same bar. For a gap through the stop, use the worse open.
            if stop_hit:
                raw_exit = min(stop, bar["o"]) if direction == 1 else max(stop, bar["o"])
                fill = _adverse_fill(raw_exit, direction, entry=False, slippage_rate=slippage_rate)
                gross_pnl += (fill - entry) * direction * remaining
                fees += fill * remaining * fee_rate
                exits.append({"t": bar["t"], "kind": "STOP", "price": fill, "qty": remaining})
                remaining = 0.0
                outcome = "STOP"
                break

            if tp1_hit and remaining > qty * 0.5000001:
                take_qty = qty * 0.5
                fill = _adverse_fill(tp1, direction, entry=False, slippage_rate=slippage_rate)
                gross_pnl += (fill - entry) * direction * take_qty
                fees += fill * take_qty * fee_rate
                remaining -= take_qty
                exits.append({"t": bar["t"], "kind": "TP1", "price": fill, "qty": take_qty})

            if tp2_hit:
                fill = _adverse_fill(tp2, direction, entry=False, slippage_rate=slippage_rate)
                gross_pnl += (fill - entry) * direction * remaining
                fees += fill * remaining * fee_rate
                exits.append({"t": bar["t"], "kind": "TP2", "price": fill, "qty": remaining})
                remaining = 0.0
                outcome = "TP2" if len(exits) == 1 else "TP1+TP2"
                break

            j += 1

        if remaining > 0:
            bar = rows[-1]
            fill = _adverse_fill(bar["c"], direction, entry=False, slippage_rate=slippage_rate)
            gross_pnl += (fill - entry) * direction * remaining
            fees += fill * remaining * fee_rate
            exits.append({"t": bar["t"], "kind": "END_OF_DATA", "price": fill, "qty": remaining})
            remaining = 0.0

        net_pnl = gross_pnl - fees
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
            "entry_price": entry, "stop": stop, "tp1": tp1, "tp2": tp2,
            "signal_score": result["score"], "gross_pnl": gross_pnl, "fees": fees,
            "net_pnl": net_pnl, "equity_before": equity_before, "equity_after": equity,
            "return_on_equity_pct": net_pnl / equity_before * 100 if equity_before else None,
            "exit_time": exits[-1]["t"], "exit_kind": outcome, "exits": exits,
        })
        equity_curve.append({"t": exits[-1]["t"], "equity": equity})
        i = max(j + 1, i + 1)
        if equity <= 0:
            break

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
        "net_return_pct": round((equity / initial_equity - 1) * 100, 4),
        "max_drawdown_pct": round(max_dd * 100, 4),
        "equity_final": round(equity, 8),
        "assumptions": {"fee_rate_per_side": fee_rate, "slippage_rate_per_side": slippage_rate,
                        "risk_fraction": risk_fraction, "max_leverage": max_leverage,
                        "entry": "next candle open", "same_bar_stop_target": "stop first",
                        "partial_targets": "50% at 2R, remaining 50% at 3R",
                        "funding_costs_included": False},
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
