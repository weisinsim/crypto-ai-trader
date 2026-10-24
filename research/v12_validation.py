"""Chronological holdout, train-only threshold calibration, expanding walk-forward, and cost stress."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from research.v12_backtest import load_feature_csv, run_v12_backtest, MODEL_VERSION
from research.v12_hybrid_engine import ASSET_PROFILES, WEIGHTS
from research.run_backtest import load_funding_csv


def _metrics(result):
    return {key: result[key] for key in (
        "trades", "wins", "losses", "win_rate_pct", "profit_factor",
        "funding_pnl_total", "net_return_pct", "max_drawdown_pct", "equity_final",
    )}


def _calibrate_threshold(train_rows, symbol, common):
    base_score = int(ASSET_PROFILES.get(symbol, {}).get("min_score", 75))
    score_candidates = sorted({base_score, min(base_score + 5, 95),
                               min(base_score + 10, 95), min(base_score + 15, 95)})
    short_rsi_floors = (None, 30.0)
    candidates = []
    eligible = []
    for min_score in score_candidates:
        for short_rsi_floor in short_rsi_floors:
            result = run_v12_backtest(
                train_rows, symbol, **common,
                min_score_override=min_score, min_edge_override=8,
                short_rsi_floor=short_rsi_floor,
            )
            pf = result["profit_factor"]
            item = {
                "min_score": min_score, "min_edge": 8, "short_rsi_floor": short_rsi_floor,
                "trades": result["trades"], "win_rate_pct": result["win_rate_pct"],
                "profit_factor": pf, "net_return_pct": result["net_return_pct"],
                "max_drawdown_pct": result["max_drawdown_pct"],
            }
            candidates.append(item)
            if (
                result["trades"] >= 20
                and isinstance(pf, (int, float)) and math.isfinite(pf) and pf > 1.0
                and result["net_return_pct"] > 0
                and result["max_drawdown_pct"] <= 15
            ):
                eligible.append(item)
    selected = max(
        eligible,
        key=lambda item: (item["net_return_pct"], item["profit_factor"], -item["max_drawdown_pct"]),
        default=None,
    )
    return {
        "base_min_score": base_score, "minimum_training_trades": 20,
        "eligibility": "PF > 1, positive net return, max drawdown <= 15%",
        "candidates": candidates, "selected": selected,
        "status": "TRAINING_CANDIDATE_SELECTED" if selected else "NO_CANDIDATE_PASSED_TRAINING_GATES",
    }


def _overrides(calibration):
    selected = calibration.get("selected")
    return (
        {"min_score_override": selected["min_score"], "min_edge_override": selected["min_edge"], "short_rsi_floor": selected["short_rsi_floor"]}
        if selected else {}
    )


def _aggregate_walk_forward(rows, symbol, *, split, folds, wf_start, common, initial_equity):
    available = split - wf_start
    if available < folds * 3:
        raise ValueError("not enough pre-holdout rows for requested walk-forward folds")

    current_equity = initial_equity
    aggregate_curve = [{"t": rows[wf_start]["t"], "equity": initial_equity, "kind": "initial"}]
    all_trades = []
    fold_reports = []

    for fold in range(folds):
        start = wf_start + (available * fold) // folds
        end = wf_start + (available * (fold + 1)) // folds
        if end - start < 3:
            raise ValueError("walk-forward test fold has fewer than three rows")

        # Expanding-window walk-forward: train only on candles before this fold,
        # calibrate the thresholds there, then test only on the next contiguous window.
        train_prefix = rows[:start]
        calibration = _calibrate_threshold(train_prefix, symbol, common)
        test_overrides = _overrides(calibration)
        fold_common = dict(common)
        fold_common["initial_equity"] = current_equity
        result = run_v12_backtest(rows[start:end], symbol, **fold_common, **test_overrides)
        current_equity = result["equity_final"]
        all_trades.extend(result["trade_log"])
        aggregate_curve.extend(result["equity_curve"][1:])
        fold_reports.append({
            "fold": fold + 1,
            "train_start_ts": rows[0]["t"], "train_end_ts": rows[start - 1]["t"],
            "test_start_ts": rows[start]["t"], "test_end_ts": rows[end - 1]["t"],
            "train_rows": start, "test_rows": end - start,
            "training_calibration": {
                "status": calibration["status"],
                "selected": calibration["selected"],
            },
            "test_metrics": _metrics(result),
        })

    peak = initial_equity
    max_drawdown = 0.0
    for point in aggregate_curve:
        peak = max(peak, point["equity"])
        dd = (peak - point["equity"]) / peak if peak > 0 else 1.0
        max_drawdown = max(max_drawdown, dd)

    wins = [trade for trade in all_trades if trade["net_pnl"] > 0]
    losses = [trade for trade in all_trades if trade["net_pnl"] < 0]
    gross_profit = sum(trade["net_pnl"] for trade in wins)
    gross_loss = -sum(trade["net_pnl"] for trade in losses)
    aggregate = {
        "trades": len(all_trades), "wins": len(wins), "losses": len(losses),
        "win_rate_pct": round(len(wins) / len(all_trades) * 100, 4) if all_trades else None,
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else (None if not gross_profit else "INF"),
        "funding_pnl_total": round(sum(trade["funding_pnl"] for trade in all_trades), 8),
        "net_return_pct": round((current_equity / initial_equity - 1) * 100, 4),
        "max_drawdown_pct": round(max_drawdown * 100, 4),
        "equity_final": round(current_equity, 8),
    }
    return fold_reports, aggregate


def _fixed_weight_walk_forward(rows, symbol, *, split, folds, wf_start, common, initial_equity, weights):
    """Evaluate fixed component weights on expanding chronological test windows."""
    available = split - wf_start
    if available < folds * 3:
        raise ValueError("not enough pre-holdout rows for component ablation")
    current_equity = initial_equity
    aggregate_curve = [{"t": rows[wf_start]["t"], "equity": initial_equity, "kind": "initial"}]
    all_trades = []
    for fold in range(folds):
        start = wf_start + (available * fold) // folds
        end = wf_start + (available * (fold + 1)) // folds
        fold_common = dict(common)
        fold_common["initial_equity"] = current_equity
        result = run_v12_backtest(
            rows[start:end], symbol, **fold_common,
            component_weights_override=weights,
        )
        current_equity = result["equity_final"]
        all_trades.extend(result["trade_log"])
        aggregate_curve.extend(result["equity_curve"][1:])

    peak = initial_equity
    max_drawdown = 0.0
    for point in aggregate_curve:
        peak = max(peak, point["equity"])
        dd = (peak - point["equity"]) / peak if peak > 0 else 1.0
        max_drawdown = max(max_drawdown, dd)
    wins = [trade for trade in all_trades if trade["net_pnl"] > 0]
    losses = [trade for trade in all_trades if trade["net_pnl"] < 0]
    gross_profit = sum(trade["net_pnl"] for trade in wins)
    gross_loss = -sum(trade["net_pnl"] for trade in losses)
    return {
        "trades": len(all_trades), "wins": len(wins), "losses": len(losses),
        "win_rate_pct": round(len(wins) / len(all_trades) * 100, 4) if all_trades else None,
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else (None if not gross_profit else "INF"),
        "funding_pnl_total": round(sum(trade["funding_pnl"] for trade in all_trades), 8),
        "net_return_pct": round((current_equity / initial_equity - 1) * 100, 4),
        "max_drawdown_pct": round(max_drawdown * 100, 4),
        "equity_final": round(current_equity, 8),
    }


def _component_ablation(rows, symbol, *, split, folds, wf_start, common, initial_equity):
    baseline_weights = dict(WEIGHTS)
    results = {
        "full_ensemble_fixed_threshold": _fixed_weight_walk_forward(
            rows, symbol, split=split, folds=folds, wf_start=wf_start,
            common=common, initial_equity=initial_equity, weights=baseline_weights,
        )
    }
    for component in WEIGHTS:
        weights = dict(WEIGHTS)
        weights[component] = 0.0
        results[f"without_{component}"] = _fixed_weight_walk_forward(
            rows, symbol, split=split, folds=folds, wf_start=wf_start,
            common=common, initial_equity=initial_equity, weights=weights,
        )
    baseline = results["full_ensemble_fixed_threshold"]
    deltas = {}
    for name, metrics in results.items():
        if name == "full_ensemble_fixed_threshold":
            continue
        deltas[name] = {
            "net_return_delta_pct_points": round(metrics["net_return_pct"] - baseline["net_return_pct"], 4),
            "profit_factor_delta": (
                round(metrics["profit_factor"] - baseline["profit_factor"], 4)
                if isinstance(metrics["profit_factor"], (int, float))
                and isinstance(baseline["profit_factor"], (int, float)) else None
            ),
            "trade_count_delta": metrics["trades"] - baseline["trades"],
        }
    return {
        "method": "fixed per-coin default score threshold; same chronological pre-holdout test segments (50%-80%), no per-variant retuning",
        "note": "Ablation diagnostics only; thresholds are not re-tuned per variant and results are not production approval.",
        "results": results, "delta_vs_full_ensemble": deltas,
    }


WEIGHT_PROFILE_CANDIDATES = {
    "default": dict(WEIGHTS),
    "trend_light": {
        "momentum_funding": 0.35, "trend_breakout": 0.15,
        "confluence": 0.30, "asset_adaptive": 0.20,
    },
    "trend_off": {
        "momentum_funding": 0.4285714286, "trend_breakout": 0.0,
        "confluence": 0.3571428571, "asset_adaptive": 0.2142857143,
    },
}


def _select_weight_profile(train_rows, symbol, common):
    candidates = []
    eligible = []
    for name, weights in WEIGHT_PROFILE_CANDIDATES.items():
        result = run_v12_backtest(
            train_rows, symbol, **common, component_weights_override=weights,
        )
        pf = result["profit_factor"]
        item = {
            "profile": name, "weights": weights,
            "trades": result["trades"], "win_rate_pct": result["win_rate_pct"],
            "profit_factor": pf, "net_return_pct": result["net_return_pct"],
            "max_drawdown_pct": result["max_drawdown_pct"],
        }
        candidates.append(item)
        if (
            result["trades"] >= 20
            and isinstance(pf, (int, float)) and math.isfinite(pf) and pf > 1.0
            and result["net_return_pct"] > 0
            and result["max_drawdown_pct"] <= 15
        ):
            eligible.append(item)
    selected = max(
        eligible,
        key=lambda item: (item["profit_factor"], item["net_return_pct"], -item["max_drawdown_pct"]),
        default=None,
    )
    return {
        "selected": selected or next(item for item in candidates if item["profile"] == "default"),
        "selection_status": "TRAINING_CANDIDATE_SELECTED" if selected else "DEFAULT_PROFILE_RETAINED",
        "eligibility": "at least 20 trades, PF > 1, positive return, max drawdown <= 15%",
        "candidates": candidates,
    }


def _weight_profile_walk_forward(rows, symbol, *, split, folds, wf_start, common, initial_equity):
    available = split - wf_start
    if available < folds * 3:
        raise ValueError("not enough pre-holdout rows for weight-profile walk-forward")
    current_equity = initial_equity
    aggregate_curve = [{"t": rows[wf_start]["t"], "equity": initial_equity, "kind": "initial"}]
    all_trades = []
    fold_reports = []
    for fold in range(folds):
        start = wf_start + (available * fold) // folds
        end = wf_start + (available * (fold + 1)) // folds
        selection = _select_weight_profile(rows[:start], symbol, common)
        selected = selection["selected"]
        fold_common = dict(common)
        fold_common["initial_equity"] = current_equity
        result = run_v12_backtest(
            rows[start:end], symbol, **fold_common,
            component_weights_override=selected["weights"],
        )
        current_equity = result["equity_final"]
        all_trades.extend(result["trade_log"])
        aggregate_curve.extend(result["equity_curve"][1:])
        fold_reports.append({
            "fold": fold + 1,
            "train_rows": start, "test_rows": end - start,
            "test_start_ts": rows[start]["t"], "test_end_ts": rows[end - 1]["t"],
            "selection_status": selection["selection_status"],
            "selected_profile": selected["profile"],
            "selected_training_metrics": {k: selected[k] for k in (
                "trades", "profit_factor", "net_return_pct", "max_drawdown_pct",
            )},
            "test_metrics": _metrics(result),
        })
    peak = initial_equity
    max_drawdown = 0.0
    for point in aggregate_curve:
        peak = max(peak, point["equity"])
        dd = (peak - point["equity"]) / peak if peak > 0 else 1.0
        max_drawdown = max(max_drawdown, dd)
    wins = [trade for trade in all_trades if trade["net_pnl"] > 0]
    losses = [trade for trade in all_trades if trade["net_pnl"] < 0]
    gross_profit = sum(trade["net_pnl"] for trade in wins)
    gross_loss = -sum(trade["net_pnl"] for trade in losses)
    aggregate = {
        "trades": len(all_trades), "wins": len(wins), "losses": len(losses),
        "win_rate_pct": round(len(wins) / len(all_trades) * 100, 4) if all_trades else None,
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else (None if not gross_profit else "INF"),
        "funding_pnl_total": round(sum(trade["funding_pnl"] for trade in all_trades), 8),
        "net_return_pct": round((current_equity / initial_equity - 1) * 100, 4),
        "max_drawdown_pct": round(max_drawdown * 100, 4),
        "equity_final": round(current_equity, 8),
    }
    return fold_reports, aggregate


def run_v12_validation(rows, symbol, *, holdout_fraction=0.2, folds=4,
                       fee_rate=0.0005, slippage_rate=0.0002,
                       initial_equity=1.0, risk_fraction=0.005, max_leverage=3.0,
                       funding_events=None, trailing_atr_multiple=1.5,
                       max_holding_bars=72, signal_reversal_exit=True):
    if not math.isfinite(holdout_fraction) or not 0.1 <= holdout_fraction <= 0.4:
        raise ValueError("holdout_fraction must be between 0.1 and 0.4")
    if not isinstance(folds, int) or isinstance(folds, bool) or folds < 2:
        raise ValueError("folds must be an integer >= 2")
    if len(rows) < max(30, folds * 5):
        raise ValueError("not enough feature rows for holdout and forward evaluation")

    symbol = str(symbol).upper()
    split = int(len(rows) * (1 - holdout_fraction))
    wf_start = max(int(len(rows) * 0.5), 1)
    if split < 10 or len(rows) - split < 5:
        raise ValueError("insufficient rows in chronological holdout split")
    common = {
        "fee_rate": fee_rate, "slippage_rate": slippage_rate,
        "initial_equity": initial_equity, "risk_fraction": risk_fraction,
        "max_leverage": max_leverage, "funding_events": list(funding_events or []),
        "trailing_atr_multiple": trailing_atr_multiple,
        "max_holding_bars": max_holding_bars, "signal_reversal_exit": signal_reversal_exit,
    }

    full = run_v12_backtest(rows, symbol, **common)
    train_rows = rows[:split]
    holdout_rows = rows[split:]
    baseline_holdout = run_v12_backtest(holdout_rows, symbol, **common)

    calibration = _calibrate_threshold(train_rows, symbol, common)
    selected_overrides = _overrides(calibration)
    calibrated_holdout = (
        run_v12_backtest(holdout_rows, symbol, **common, **selected_overrides)
        if calibration["selected"] else baseline_holdout
    )

    # The final holdout is outside both threshold selection and walk-forward test windows.
    walk_forward, walk_forward_aggregate = _aggregate_walk_forward(
        rows, symbol, split=split, folds=folds, wf_start=wf_start,
        common=common, initial_equity=initial_equity,
    )
    component_ablation = _component_ablation(
        rows, symbol, split=split, folds=folds, wf_start=wf_start,
        common=common, initial_equity=initial_equity,
    )
    weight_profile_selection = _select_weight_profile(train_rows, symbol, common)
    selected_weight_profile = weight_profile_selection["selected"]
    weight_profile_holdout = run_v12_backtest(
        holdout_rows, symbol, **common,
        component_weights_override=selected_weight_profile["weights"],
    )
    weight_profile_folds, weight_profile_aggregate = _weight_profile_walk_forward(
        rows, symbol, split=split, folds=folds, wf_start=wf_start,
        common=common, initial_equity=initial_equity,
    )

    cost_stress = {}
    for name, multiplier in (("base", 1.0), ("stress_1_5x", 1.5), ("severe_2x", 2.0)):
        result = run_v12_backtest(
            holdout_rows, symbol, fee_rate=fee_rate * multiplier,
            slippage_rate=slippage_rate * multiplier, initial_equity=initial_equity,
            risk_fraction=risk_fraction, max_leverage=max_leverage,
            funding_events=list(funding_events or []), **selected_overrides,
        )
        cost_stress[name] = _metrics(result)

    holdout_sample_gate = (
        "SUFFICIENT_FOR_SCREENING" if calibrated_holdout["trades"] >= 20
        else "INSUFFICIENT_HOLDOUT_TRADES"
    )
    return {
        "model_version": MODEL_VERSION, "symbol": symbol,
        "status": "RESEARCH_ONLY", "profitability_claim_eligible": False,
        "strategy_approval": False, "live_trading_enabled": False,
        "dataset": {"rows": len(rows), "first_ts": rows[0]["t"], "last_ts": rows[-1]["t"]},
        "methodology": {
            "holdout_fraction": holdout_fraction, "holdout_start_index": split,
            "walk_forward_folds": folds, "walk_forward_start_index": wf_start,
            "walk_forward_end_index": split,
            "parameter_policy": "expanding-window threshold calibration uses only data before each test fold; final holdout is never used for selection",
            "note": "Walk-forward test folds cover the pre-holdout period from 50% to the training/holdout boundary. Final holdout is reported separately.",
        },
        "full_sample": {key: full[key] for key in (
            "trades", "win_rate_pct", "profit_factor", "funding_pnl_total", "net_return_pct", "max_drawdown_pct",
        )},
        "holdout": {key: baseline_holdout[key] for key in (
            "trades", "win_rate_pct", "profit_factor", "funding_pnl_total", "net_return_pct",
            "max_drawdown_pct", "equity_final", "trade_log",
        )},
        "threshold_calibration": {
            "training_rows": split, **calibration,
        },
        "calibrated_holdout": {key: calibrated_holdout[key] for key in (
            "trades", "win_rate_pct", "profit_factor", "funding_pnl_total", "net_return_pct",
            "max_drawdown_pct", "equity_final", "trade_log",
        )},
        "holdout_sample_gate": holdout_sample_gate,
        "walk_forward_folds": walk_forward,
        "walk_forward_aggregate": walk_forward_aggregate,
        "component_ablation": component_ablation,
        "weight_profile_selection": weight_profile_selection,
        "weight_profile_holdout": {key: weight_profile_holdout[key] for key in (
            "trades", "win_rate_pct", "profit_factor", "funding_pnl_total", "net_return_pct",
            "max_drawdown_pct", "equity_final",
        )},
        "weight_profile_walk_forward_folds": weight_profile_folds,
        "weight_profile_walk_forward_aggregate": weight_profile_aggregate,
        "holdout_cost_stress": cost_stress,
        "limitations": [
            "Funding cash flows are included when validated funding events are supplied; endpoint mark prices are not reconciled against an account ledger.",
            "OI, CVD and spread are unavailable unless independently aligned data is added.",
            "Threshold selection is limited to a small predefined grid and is not proof of generalization.",
            "The final holdout requires enough trades and must be followed by paper trading before any production consideration.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, help="causally precomputed V12 feature CSV")
    parser.add_argument("--funding-csv", help="optional aligned historical funding settlement CSV")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--fee-rate", type=float, default=0.0005)
    parser.add_argument("--slippage-rate", type=float, default=0.0002)
    parser.add_argument("--initial-equity", type=float, default=1.0)
    parser.add_argument("--risk-fraction", type=float, default=0.005)
    parser.add_argument("--max-leverage", type=float, default=3.0)
    parser.add_argument("--trailing-atr-multiple", type=float, default=1.5)
    parser.add_argument("--max-holding-bars", type=int, default=72)
    parser.add_argument("--disable-signal-reversal-exit", action="store_true")
    parser.add_argument("--holdout-fraction", type=float, default=0.2)
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    source = Path(args.csv).resolve()
    output = Path(args.output).resolve()
    if source == output:
        raise ValueError("output must not overwrite input CSV")
    rows = load_feature_csv(str(source))
    funding_events = load_funding_csv(args.funding_csv) if args.funding_csv else []
    if funding_events and funding_events[0]["symbol"] != args.symbol.upper():
        raise ValueError("funding CSV symbol does not match --symbol")
    report = run_v12_validation(
        rows, args.symbol, holdout_fraction=args.holdout_fraction, folds=args.folds,
        fee_rate=args.fee_rate, slippage_rate=args.slippage_rate,
        initial_equity=args.initial_equity, risk_fraction=args.risk_fraction,
        max_leverage=args.max_leverage, funding_events=funding_events,
        trailing_atr_multiple=args.trailing_atr_multiple,
        max_holding_bars=args.max_holding_bars,
        signal_reversal_exit=not args.disable_signal_reversal_exit,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
