"""Run a reproducible EMA baseline over CSV OHLCV with chronological holdout evaluation."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
from statistics import mean

from research.backtest import run_ema_cross_backtest, validate_candles
from research.funding_sensitivity import summarize_funding_scenarios
from research.cost_stress import run_cost_stress_scenarios


def source_file_manifest(path: str | None) -> dict:
    """Fingerprint the exact input bytes without claiming that the source is authentic."""
    if path is None:
        return {"provided": False, "sha256": None, "size_bytes": None}
    digest = hashlib.sha256()
    size_bytes = 0
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size_bytes += len(chunk)
    return {
        "provided": True,
        "sha256": digest.hexdigest(),
        "size_bytes": size_bytes,
        "source_authenticity_verified": False,
    }


def load_csv(path: str, interval_ms: int) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as handle:
        rows = []
        for row in csv.DictReader(handle):
            rows.append({
                "t": int(row["t"]), "o": float(row["o"]), "h": float(row["h"]),
                "l": float(row["l"]), "c": float(row["c"]),
                "v": float(row.get("v", 0) or 0), "q": float(row.get("q", 0) or 0),
            })
    return validate_candles(rows, interval_ms=interval_ms)


def load_funding_csv(path: str) -> list[dict]:
    # Binance API settlement timestamps can contain millisecond-level jitter.
    # Normalize only timestamps within 1 second of the canonical 8h boundary;
    # preserve the raw exchange timestamp for auditability. Larger deviations fail closed.
    from research.funding_data import canonical_settlement_timestamp
    def normalize_settlement_timestamp(raw_ts: int) -> int:
        # Only snap timestamps already within 1 second of a canonical 8-hour
        # boundary. Preserve legitimate non-grid timestamps for other schedules.
        try:
            return canonical_settlement_timestamp(raw_ts, tolerance_ms=1000)
        except ValueError:
            return raw_ts

    with open(path, newline="", encoding="utf-8") as handle:
        rows = [{"funding_time_raw": int(row["funding_time"]),
                 "funding_time": normalize_settlement_timestamp(int(row["funding_time"])),
                 "funding_rate": float(row["funding_rate"]),
                 "mark_price": float(row["mark_price"]),
                 "symbol": row.get("symbol", "").strip().upper(),
                 "mark_price_source": row.get("mark_price_source", "unspecified").strip() or "unspecified",
                 "venue": row.get("venue", "").strip().lower()}
                for row in csv.DictReader(handle)]
    symbols = {row["symbol"] for row in rows}
    if not rows or "" in symbols or len(symbols) != 1:
        raise ValueError("funding CSV must contain rows for exactly one non-empty symbol")
    if any(row["funding_time"] < 0 for row in rows):
        raise ValueError("funding CSV contains negative settlement timestamps")
    if any(not math.isfinite(row["funding_rate"]) or
           not math.isfinite(row["mark_price"]) or row["mark_price"] <= 0 for row in rows):
        raise ValueError("funding CSV contains non-finite rates or invalid mark prices")
    rows.sort(key=lambda row: row["funding_time"])
    for left, right in zip(rows, rows[1:]):
        if left["funding_time"] == right["funding_time"]:
            raise ValueError(f"duplicate funding timestamp: {left['funding_time']}")
    return rows



def summarize_funding_data_quality(
    funding_rows: list[dict] | None, candle_rows: list[dict]
) -> dict:
    """Describe funding timestamp coverage without assuming a fixed settlement cadence."""
    if not funding_rows:
        return {
            "provided": False,
            "records_in_candle_range": 0,
            "exact_candle_open_matches": 0,
            "non_candle_open_events": 0,
            "source_labels": [],
            "venue_labels": [],
            "event_interval_ms": [],
            "event_completeness_verified": False,
            "review_required": True,
            "audit_status": "REVIEW_REQUIRED",
            "interpretation": "No historical funding events supplied; no coverage or settlement claim can be made.",
        }
    candle_timestamps = {row["t"] for row in candle_rows}
    source_counts: dict[str, int] = {}
    for row in funding_rows:
        label = str(row.get("mark_price_source", "unspecified") or "unspecified")
        source_counts[label] = source_counts.get(label, 0) + 1
    source_labels = sorted(source_counts)
    proxy_mark_price_records = sum(
        count for label, count in source_counts.items() if "PROXY" in label.upper()
    )
    proxy_mark_price_review_required = proxy_mark_price_records > 0
    # Preserve raw timestamps for cadence diagnostics. For candle alignment only,
    # use the already-normalized timestamp when the raw jitter is <=1s and the
    # normalized timestamp is an actual candle open.
    timestamp_pairs = []
    for row in funding_rows:
        raw_ts = int(row.get("funding_time_raw", row["funding_time"]))
        normalized_ts = int(row["funding_time"])
        alignment_ts = (
            normalized_ts
            if normalized_ts != raw_ts
            and abs(normalized_ts - raw_ts) <= 1000
            and normalized_ts in candle_timestamps
            else raw_ts
        )
        timestamp_pairs.append((raw_ts, alignment_ts))
    timestamp_pairs.sort(key=lambda pair: pair[0])
    event_timestamps = [raw_ts for raw_ts, _ in timestamp_pairs]
    timestamp_counts = {}
    for timestamp in event_timestamps:
        timestamp_counts[timestamp] = timestamp_counts.get(timestamp, 0) + 1
    duplicate_timestamps = sorted(
        timestamp for timestamp, count in timestamp_counts.items() if count > 1
    )
    start_ts, end_ts = min(candle_timestamps), max(candle_timestamps)
    in_range_pairs = [
        (raw_ts, align_ts) for raw_ts, align_ts in timestamp_pairs
        if start_ts <= raw_ts <= end_ts
    ]
    deltas = [right - left for left, right in zip(event_timestamps, event_timestamps[1:])]
    raw_matched = sum(raw_ts in candle_timestamps for raw_ts, _ in in_range_pairs)
    matched = sum(align_ts in candle_timestamps for _, align_ts in in_range_pairs)
    jitter_normalized = sum(
        raw_ts != align_ts for raw_ts, align_ts in in_range_pairs
    )
    nonmatching = len(in_range_pairs) - matched
    out_of_range = len(event_timestamps) - len(in_range_pairs)
    duplicate_event_records = sum(count - 1 for count in timestamp_counts.values() if count > 1)
    needs_review = bool(duplicate_timestamps or out_of_range or nonmatching)
    return {
        "provided": True,
        "records_in_candle_range": len(in_range_pairs),
        "records_outside_candle_range": out_of_range,
        "duplicate_event_records": duplicate_event_records,
        "duplicate_event_timestamps": duplicate_timestamps,
        "first_event_ts": event_timestamps[0],
        "last_event_ts": event_timestamps[-1],
        "exact_candle_open_matches": raw_matched,
        "normalized_candle_open_matches": matched,
        "boundary_jitter_normalized_records": jitter_normalized,
        "non_candle_open_events": nonmatching,
        "source_labels": source_labels,
        "source_record_counts": dict(sorted(source_counts.items())),
        "proxy_mark_price_records": proxy_mark_price_records,
        "proxy_mark_price_review_required": proxy_mark_price_review_required,
        "venue_labels": sorted({row.get("venue", "") or "unspecified" for row in funding_rows}),
        "event_interval_ms": {
            "min": min(deltas) if deltas else None,
            "max": max(deltas) if deltas else None,
            "distinct_count": len(set(deltas)),
        },
        "event_completeness_verified": False,
        "review_required": needs_review,
        "audit_status": (
            "REVIEW_REQUIRED" if needs_review else (
                "BOUNDARY_JITTER_NORMALIZED_ALIGNMENT_PASS_NOT_RECONCILED"
                if jitter_normalized else "TIMESTAMP_ALIGNMENT_PASS_NOT_RECONCILED"
            )
        ),
        "interpretation": (
            "Raw exchange timestamps and cadence are preserved. Alignment uses normalized timestamps only "
            "when raw jitter is <=1 second and the normalized timestamp is an actual candle open. "
            "Alignment results do not prove event completeness, actual mark-price provenance, "
            "or position eligibility at settlement."
        ),
    }


def summarize_holdout_trade_quality(trades: list[dict]) -> dict:
    """Summarize holdout trade outcomes by side, exit reason and holding duration.

    These are descriptive diagnostics over the existing net PnL field. They do not
    attribute fees/slippage separately because the trade log does not expose those
    components as independently reconciled cashflows.
    """
    def group_summary(selected: list[dict]) -> dict:
        pnls = [float(trade["pnl_equity"]) for trade in selected]
        wins = [pnl for pnl in pnls if pnl > 0]
        losses = [-pnl for pnl in pnls if pnl < 0]
        funding_cashflows = [
            float(trade.get("funding_cashflow", 0.0))
            for trade in selected
            if _finite_metric(trade.get("funding_cashflow", 0.0))
        ]
        return {
            "trades": len(selected),
            "wins": len(wins),
            "losses": len(losses),
            "breakeven": sum(pnl == 0 for pnl in pnls),
            "win_rate_pct": round(len(wins) / len(selected) * 100, 2) if selected else None,
            "net_pnl_equity": round(sum(pnls), 8),
            "profit_factor": round(sum(wins) / sum(losses), 4) if losses else (None if not wins else "INF"),
            "mean_pnl_equity": round(mean(pnls), 8) if pnls else None,
            "mean_bars_held": round(mean(float(trade.get("bars_held", 0)) for trade in selected), 2) if selected else None,
            "funding_cashflow_total": round(sum(funding_cashflows), 8),
            "funding_boundary_ambiguous_trades": sum(bool(trade.get("funding_boundary_ambiguous")) for trade in selected),
        }

    def summarize_groups(key_fn) -> dict:
        keys = sorted({str(key_fn(trade)) for trade in trades})
        return {key: group_summary([trade for trade in trades if str(key_fn(trade)) == key]) for key in keys}

    return {
        "status": "DIAGNOSTIC_ONLY",
        "by_side": summarize_groups(lambda trade: trade.get("side", "UNKNOWN")),
        "by_exit_reason": summarize_groups(lambda trade: trade.get("reason", "UNKNOWN")),
        "by_holding_duration": {
            "0_3_bars": group_summary([trade for trade in trades if 0 <= int(trade.get("bars_held", 0)) <= 3]),
            "4_12_bars": group_summary([trade for trade in trades if 4 <= int(trade.get("bars_held", 0)) <= 12]),
            "13_plus_bars": group_summary([trade for trade in trades if int(trade.get("bars_held", 0)) >= 13]),
        },
        "limitations": [
            "Net PnL is reported as emitted by the simulator; fee and slippage attribution is not separately reconstructed.",
            "Funding cashflow is the simulator's applied cashflow and does not prove exchange-account reconciliation.",
            "Small groups are descriptive only and should not be treated as statistically significant.",
        ],
    }


def build_walk_forward_windows(n: int, start_eval: int, folds: int, min_bars: int) -> list[tuple[int, int]]:
    """Build contiguous, non-overlapping forward windows or fail if a fold is too short."""
    if n < 0 or start_eval < 0 or start_eval >= n:
        raise ValueError("start_eval must be inside the available data")
    if folds < 2:
        raise ValueError("folds must be >= 2")
    if min_bars < 1:
        raise ValueError("min_bars must be >= 1")
    eval_width = (n - start_eval) // folds
    if eval_width < min_bars:
        raise ValueError(
            f"insufficient bars per walk-forward fold: available={n - start_eval}, "
            f"folds={folds}, minimum_per_fold={min_bars}"
        )
    windows = []
    for fold in range(folds):
        a = start_eval + fold * eval_width
        b = n if fold == folds - 1 else a + eval_width
        if b - a < min_bars:
            raise ValueError(f"walk-forward fold {fold + 1} is too short: {b - a} bars")
        windows.append((a, b))
    if windows[0][0] != start_eval or windows[-1][1] != n:
        raise AssertionError("walk-forward windows do not cover the evaluation range")
    if any(left[1] != right[0] for left, right in zip(windows, windows[1:])):
        raise AssertionError("walk-forward windows overlap or leave gaps")
    return windows


def max_drawdown_pct(points: list[dict], initial_equity: float = 1.0) -> float:
    """Compute peak-to-trough drawdown, including the evaluation's starting equity."""
    try:
        peak = float(initial_equity)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError("initial_equity must be finite and positive") from exc
    if not math.isfinite(peak) or peak <= 0:
        raise ValueError("initial_equity must be finite and positive")
    max_dd = 0.0
    for point in points:
        if not isinstance(point, dict) or "equity" not in point:
            raise ValueError("equity curve points must contain equity")
        try:
            value = float(point["equity"])
        except (OverflowError, TypeError, ValueError) as exc:
            raise ValueError("equity curve contains invalid values") from exc
        if not math.isfinite(value):
            raise ValueError("equity curve contains non-finite values")
        peak = max(peak, value)
        if peak > 0:
            max_dd = max(max_dd, (peak - value) / peak)
    return round(max_dd * 100, 4)


def _finite_metric(value) -> bool:
    """Accept finite numeric metrics, excluding bools and unrepresentable huge integers."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, ValueError):
        return False

def _profit_factor_above_threshold(value, threshold: float) -> bool:
    """Treat the simulator's explicit no-loss sentinel as infinite profit factor."""
    if value == "INF":
        return True
    return _finite_metric(value) and value > threshold


def summarize_walk_forward_cost_stress(walk_results: list[dict]) -> dict:
    """Aggregate fold-level cost robustness without hiding failed or missing folds."""
    scenario_names = ("base", "stress", "severe_stress")
    per_scenario = {}
    for name in scenario_names:
        rows = []
        for fold in walk_results:
            stress = fold.get("cost_stress") if isinstance(fold, dict) else None
            scenarios = stress.get("scenarios") if isinstance(stress, dict) else None
            row = scenarios.get(name, {}) if isinstance(scenarios, dict) else {}
            rows.append(row if isinstance(row, dict) else {})
        # Aggregate performance extrema only from folds and scenario runs that
        # both completed. Incomplete rows remain visible in folds_reported but
        # must not distort completed-fold performance summaries.
        completed_rows = [
            fold["cost_stress"]["scenarios"][name]
            for fold in walk_results
            if isinstance(fold, dict)
            and fold.get("status") == "COMPLETED"
            and isinstance(fold.get("cost_stress"), dict)
            and isinstance(fold["cost_stress"].get("scenarios"), dict)
            and isinstance(fold["cost_stress"]["scenarios"].get(name), dict)
            and fold["cost_stress"]["scenarios"][name].get("status") == "COMPLETED"
        ]
        valid_returns = [
            row["net_return_pct"] for row in completed_rows
            if _finite_metric(row.get("net_return_pct"))
        ]
        valid_drawdowns = [
            row["max_drawdown_pct"] for row in completed_rows
            if _finite_metric(row.get("max_drawdown_pct"))
        ]
        numeric_pf = [
            row["profit_factor"] for row in completed_rows
            if _finite_metric(row.get("profit_factor"))
        ]
        per_scenario[name] = {
            "folds_reported": len(rows),
            "folds_completed": sum(
                isinstance(fold, dict)
                and fold.get("status") == "COMPLETED"
                and isinstance(fold.get("cost_stress"), dict)
                and isinstance(fold["cost_stress"].get("scenarios"), dict)
                and isinstance(fold["cost_stress"]["scenarios"].get(name), dict)
                and fold["cost_stress"]["scenarios"][name].get("status") == "COMPLETED"
                for fold in walk_results
            ),
            "folds_positive_return": sum(
                isinstance(fold, dict)
                and fold.get("status") == "COMPLETED"
                and isinstance(fold.get("cost_stress"), dict)
                and isinstance(fold["cost_stress"].get("scenarios"), dict)
                and isinstance(fold["cost_stress"]["scenarios"].get(name), dict)
                and fold["cost_stress"]["scenarios"][name].get("status") == "COMPLETED"
                and _finite_metric(fold["cost_stress"]["scenarios"][name].get("net_return_pct"))
                and fold["cost_stress"]["scenarios"][name]["net_return_pct"] > 0
                for fold in walk_results
            ),
            "folds_pf_above_1_15": sum(
                isinstance(fold, dict)
                and fold.get("status") == "COMPLETED"
                and isinstance(fold.get("cost_stress"), dict)
                and isinstance(fold["cost_stress"].get("scenarios"), dict)
                and isinstance(fold["cost_stress"]["scenarios"].get(name), dict)
                and fold["cost_stress"]["scenarios"][name].get("status") == "COMPLETED"
                and _profit_factor_above_threshold(
                    fold["cost_stress"]["scenarios"][name].get("profit_factor"), 1.15
                )
                for fold in walk_results
            ),
            "worst_net_return_pct": round(min(valid_returns), 4) if valid_returns else None,
            "worst_max_drawdown_pct": round(max(valid_drawdowns), 4) if valid_drawdowns else None,
            "lowest_numeric_profit_factor": round(min(numeric_pf), 4) if numeric_pf else None,
        }
    def _fold_stress_is_complete_and_valid(fold: dict) -> bool:
        if not isinstance(fold, dict) or fold.get("status") != "COMPLETED":
            return False
        stress = fold.get("cost_stress")
        if not isinstance(stress, dict):
            return False
        scenarios = stress.get("scenarios")
        if not isinstance(scenarios, dict) or any(name not in scenarios for name in scenario_names):
            return False
        for name in scenario_names:
            row = scenarios.get(name)
            if not isinstance(row, dict) or row.get("status") != "COMPLETED":
                return False
            for metric in ("net_return_pct", "max_drawdown_pct", "profit_factor"):
                value = row.get(metric)
                if metric == "profit_factor":
                    if value != "INF" and not _finite_metric(value):
                        return False
                elif not _finite_metric(value):
                    return False
            if row["max_drawdown_pct"] < 0 or row["max_drawdown_pct"] > 100:
                return False
            # Re-check the actual scenario metrics rather than trusting a summary flag.
            if row["net_return_pct"] <= 0 or not _profit_factor_above_threshold(row["profit_factor"], 1.15):
                return False
            if row.get("equity_floor_hit") is not False:
                return False
        screen = stress.get("robustness_screen")
        return isinstance(screen, dict) and screen.get("pass") is True

    missing_stress = sum(
        not isinstance(fold, dict)
        or not isinstance(fold.get("cost_stress"), dict)
        or not isinstance(fold.get("cost_stress", {}).get("scenarios"), dict)
        or any(name not in fold.get("cost_stress", {}).get("scenarios", {}) for name in scenario_names)
        for fold in walk_results
    )
    passing_stress = sum(_fold_stress_is_complete_and_valid(fold) for fold in walk_results)
    minimum_trades_per_fold = 30
    under_sampled_folds = [
        fold.get("fold") if isinstance(fold, dict) else None
        for fold in walk_results
        if not isinstance(fold, dict)
        or isinstance(fold.get("trades"), bool)
        or not isinstance(fold.get("trades"), int)
        or fold.get("trades", 0) < minimum_trades_per_fold
    ]
    all_folds_stress_pass = bool(walk_results) and passing_stress == len(walk_results)
    walk_forward_gate_pass = (
        bool(walk_results)
        and missing_stress == 0
        and all_folds_stress_pass
        and not under_sampled_folds
    )
    return {
        "status": "RESEARCH_ONLY",
        "fold_count": len(walk_results),
        "folds_passing_all_three_cost_scenarios": passing_stress,
        "folds_with_missing_cost_stress": missing_stress,
        "scenario_summary": per_scenario,
        "walk_forward_candidate_gate": {
            "pass": walk_forward_gate_pass,
            "criteria": (
                "every fold has complete cost-stress data, passes base/stress/severe "
                "robustness screens, and has at least 30 trades"
            ),
            "minimum_trades_per_fold": minimum_trades_per_fold,
            "under_sampled_folds": under_sampled_folds,
            "all_folds_stress_pass": all_folds_stress_pass,
            "missing_cost_stress_folds": missing_stress,
            "promotion": False,
        },
        "interpretation": (
            "Worst-fold diagnostics; numeric PF summaries exclude non-numeric values such as INF. "
            "Missing or failed folds are not treated as passing. This candidate gate is a research "
            "screen only and is not production approval."
        ),
        "production_approval": False,
    }



def _summarize_fold_funding_boundary(
    *, rows: list[dict], end_index: int, start_ts: int, common: dict,
    symbol_end_ts: int, fold_number: int,
) -> dict:
    """Rerun one walk-forward fold under both entry-timestamp funding policies."""
    scenarios = {}
    fold_rows = rows[:end_index]
    for policy in ("exclude", "include"):
        result = run_ema_cross_backtest(
            fold_rows, trade_start_ts=start_ts,
            **{**common, "funding_entry_boundary_policy": policy},
        )
        trades = [
            trade for trade in result["trade_log"]
            if start_ts <= trade["entry_time"] <= symbol_end_ts
        ]
        curve = [
            point for point in result["equity_curve"]
            if start_ts <= point["t"] <= symbol_end_ts
        ]
        profits = sum(t["pnl_equity"] for t in trades if t["pnl_equity"] > 0)
        losses = -sum(t["pnl_equity"] for t in trades if t["pnl_equity"] < 0)
        scenarios[policy] = {
            "status": result["status"],
            "trades": len(trades),
            "net_return_pct": round((result["equity_final"] - 1.0) * 100, 4),
            "max_drawdown_pct": max_drawdown_pct(curve),
            "profit_factor": round(profits / losses, 4) if losses else (None if not profits else "INF"),
            "equity_floor_hit": result.get("equity_floor_hit", False),
            "trade_log": trades,
        }
    summary = summarize_funding_scenarios(scenarios)
    summary["comparison"] = {
        "fold": fold_number,
        "net_return_delta_include_minus_exclude_pct": round(
            scenarios["include"]["net_return_pct"] - scenarios["exclude"]["net_return_pct"], 4
        ),
        "max_drawdown_delta_include_minus_exclude_pct": round(
            scenarios["include"]["max_drawdown_pct"] - scenarios["exclude"]["max_drawdown_pct"], 4
        ),
        "interpretation": (
            "Walk-forward fold sensitivity only; neither policy establishes actual exchange "
            "settlement eligibility. OHLC does not prove intrabar event ordering."
        ),
    }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, help="closed-candle CSV from research.binance_data")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--interval", choices=("1h", "4h"), required=True,
                        help="expected candle interval; gaps are rejected")
    parser.add_argument("--train-fraction", type=float, default=0.6)
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--fast", type=int, default=20)
    parser.add_argument("--slow", type=int, default=50)
    parser.add_argument("--fee-rate", type=float, default=0.0005)
    parser.add_argument("--funding-csv", help="optional historical funding CSV with funding_time,funding_rate,mark_price,symbol")
    parser.add_argument("--slippage-rate", type=float, default=0.0002)
    parser.add_argument("--stress-multiplier", type=float, default=1.5,
                        help="fee/slippage multiplier for stress scenario (assumption, not exchange-calibrated)")
    parser.add_argument("--severe-stress-multiplier", type=float, default=2.0,
                        help="fee/slippage multiplier for severe stress scenario (assumption, not exchange-calibrated)")
    parser.add_argument("--funding-rate-per-bar", type=float, default=0.0)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    interval_ms = {"1h": 3_600_000, "4h": 14_400_000}[args.interval]
    rows = load_csv(args.csv, interval_ms)
    if not 0.3 <= args.train_fraction <= 0.8:
        raise ValueError("train-fraction must be between 0.3 and 0.8")
    if args.folds < 2:
        raise ValueError("folds must be >= 2")
    if args.fast < 2 or args.slow <= args.fast:
        raise ValueError("require 2 <= fast < slow")
    import math
    numeric_assumptions = (
        args.fee_rate, args.slippage_rate, args.funding_rate_per_bar,
        args.train_fraction, args.stress_multiplier, args.severe_stress_multiplier,
    )
    if not all(math.isfinite(x) for x in numeric_assumptions):
        raise ValueError("rates, stress multipliers and train-fraction must be finite")
    if args.fee_rate < 0 or args.slippage_rate < 0:
        raise ValueError("fee-rate and slippage-rate cannot be negative")
    if args.stress_multiplier < 1 or args.severe_stress_multiplier < args.stress_multiplier:
        raise ValueError("stress multipliers must satisfy 1 <= stress <= severe-stress")
    if len(rows) < args.slow * 3:
        raise ValueError("not enough bars for warm-up, holdout and walk-forward")

    # Keep the complete downloaded event set for data-quality diagnostics.
    # Only the in-range subset is passed to the simulator, which requires every
    # supplied settlement timestamp to belong to the candle history.
    funding_rows_for_audit = load_funding_csv(args.funding_csv) if args.funding_csv else None
    historical_funding = funding_rows_for_audit
    if historical_funding is not None and historical_funding[0]["symbol"] != args.symbol.upper():
        raise ValueError("funding CSV symbol does not match --symbol")
    if historical_funding is not None:
        historical_funding = [
            event for event in historical_funding
            if rows[0]["t"] <= event["funding_time"] <= rows[-1]["t"]
        ]
        if not historical_funding:
            raise ValueError("funding CSV has no settlement events inside the candle data range")
    common = dict(fast=args.fast, slow=args.slow, fee_rate=args.fee_rate,
                  slippage_rate=args.slippage_rate,
                  funding_rate_per_bar=args.funding_rate_per_bar,
                  funding_rates=historical_funding)
    split = int(len(rows) * args.train_fraction)
    # Use all pre-holdout history to preserve EMA/ATR state. The timestamp
    # gate excludes entries before the untouched holdout boundary.
    holdout = rows[split:]
    boundary_ts = holdout[0]["t"]
    holdout_result = run_ema_cross_backtest(rows, trade_start_ts=boundary_ts, **common)
    # Boundary sensitivity reruns the exact same candles and frozen assumptions;
    # only the ambiguous entry-time settlement policy changes.
    boundary_scenarios = {}
    for policy in ("exclude", "include"):
        policy_result = run_ema_cross_backtest(
            rows, trade_start_ts=boundary_ts,
            **{**common, "funding_entry_boundary_policy": policy},
        )
        policy_trades = [
            t for t in policy_result["trade_log"]
            if boundary_ts <= t["entry_time"] <= rows[-1]["t"]
        ]
        policy_curve = [
            p for p in policy_result["equity_curve"]
            if boundary_ts <= p["t"] <= rows[-1]["t"]
        ]
        policy_wins = sum(t["pnl_equity"] > 0 for t in policy_trades)
        policy_profit = sum(t["pnl_equity"] for t in policy_trades if t["pnl_equity"] > 0)
        policy_loss = -sum(t["pnl_equity"] for t in policy_trades if t["pnl_equity"] < 0)
        boundary_scenarios[policy] = {
            "status": policy_result["status"],
            "trades": len(policy_trades),
            "net_return_pct": round((policy_result["equity_final"] - 1.0) * 100, 4),
            "max_drawdown_pct": max_drawdown_pct(policy_curve),
            "profit_factor": round(policy_profit / policy_loss, 4) if policy_loss else (None if not policy_profit else "INF"),
            "win_rate_pct": round(policy_wins / len(policy_trades) * 100, 2) if policy_trades else None,
            "equity_floor_hit": policy_result.get("equity_floor_hit", False),
            "trade_log": policy_trades,
        }
    boundary_sensitivity = summarize_funding_scenarios(boundary_scenarios)
    boundary_sensitivity["comparison"] = {
        "net_return_delta_include_minus_exclude_pct": (
            round(boundary_scenarios["include"]["net_return_pct"] -
                  boundary_scenarios["exclude"]["net_return_pct"], 4)
            if boundary_scenarios["include"]["net_return_pct"] is not None and
               boundary_scenarios["exclude"]["net_return_pct"] is not None else None
        ),
        "max_drawdown_delta_include_minus_exclude_pct": (
            round(boundary_scenarios["include"]["max_drawdown_pct"] -
                  boundary_scenarios["exclude"]["max_drawdown_pct"], 4)
            if boundary_scenarios["include"]["max_drawdown_pct"] is not None and
               boundary_scenarios["exclude"]["max_drawdown_pct"] is not None else None
        ),
        "profit_factor_comparison": "see scenario rows; INF/null are not coerced to numeric",
        "interpretation": "Sensitivity only; neither policy establishes actual exchange settlement eligibility.",
    }
    cost_stress = run_cost_stress_scenarios(
        rows, base_fee_rate=args.fee_rate, base_slippage_rate=args.slippage_rate,
        stress_multiplier=args.stress_multiplier,
        severe_stress_multiplier=args.severe_stress_multiplier,
        backtest_kwargs={key: value for key, value in common.items()
                         if key not in ("fee_rate", "slippage_rate")},
        trade_start_ts=boundary_ts,
    )
    holdout_trades = [t for t in holdout_result["trade_log"] if boundary_ts <= t["entry_time"] <= rows[-1]["t"]]
    holdout_curve = [p for p in holdout_result["equity_curve"] if boundary_ts <= p["t"] <= rows[-1]["t"]]
    # Convert fixed-notional trade PnL to the starting-equity basis used by the runner.
    holdout_pnls = [t["pnl_equity"] for t in holdout_trades]
    holdout_profit = sum(p for p in holdout_pnls if p > 0)
    holdout_loss = -sum(p for p in holdout_pnls if p < 0)
    walk_results = []
    n = len(rows)
    # Expanding history windows with contiguous, non-overlapping forward evaluation windows.
    start_eval = max(split, args.slow * 3)
    windows = build_walk_forward_windows(n, start_eval, args.folds, args.slow + 2)
    for fold, (a, b) in enumerate(windows):
        # Expanding-window evaluation: preserve all historical indicator
        # state available before the fold, but only count entries in this fold.
        start_ts = rows[a]["t"]
        # The simulator validates every supplied funding event against its
        # candle slice. Pass only settlements available within this expanding
        # history window; future events beyond rows[:b] must not cause a false
        # timestamp-alignment failure or leak into the fold.
        fold_common = dict(common)
        if historical_funding is not None:
            fold_common["funding_rates"] = [
                event for event in historical_funding
                if rows[0]["t"] <= event["funding_time"] <= rows[b - 1]["t"]
            ]
        fold_result = run_ema_cross_backtest(rows[:b], trade_start_ts=start_ts, **fold_common)
        # Re-run the same expanding-history fold under base/stress/severe
        # execution costs. Keep fold boundaries and funding observations fixed.
        fold_cost_stress = run_cost_stress_scenarios(
            rows[:b], base_fee_rate=args.fee_rate,
            base_slippage_rate=args.slippage_rate,
            stress_multiplier=args.stress_multiplier,
            severe_stress_multiplier=args.severe_stress_multiplier,
            backtest_kwargs={key: value for key, value in fold_common.items()
                             if key not in ("fee_rate", "slippage_rate")},
            trade_start_ts=start_ts,
        )
        fold_trades = [t for t in fold_result["trade_log"] if start_ts <= t["entry_time"] <= rows[b - 1]["t"]]
        fold_curve = [p for p in fold_result["equity_curve"] if start_ts <= p["t"] <= rows[b - 1]["t"]]
        walk_results.append({
            "fold": fold + 1, "status": fold_result["status"],
            "start_ts": start_ts, "end_ts": rows[b - 1]["t"],
            "bars": b - a, "trades": len(fold_trades),
            "net_return_pct": round((fold_result["equity_final"] - 1.0) * 100, 4),
            "max_drawdown_pct": max_drawdown_pct(fold_curve),
            "equity_final": fold_result["equity_final"],
            "win_rate_pct": round(sum(t["pnl_equity"] > 0 for t in fold_trades) / len(fold_trades) * 100, 2) if fold_trades else None,
            "profit_factor": round(sum(t["pnl_equity"] for t in fold_trades if t["pnl_equity"] > 0) / -sum(t["pnl_equity"] for t in fold_trades if t["pnl_equity"] < 0), 4) if any(t["pnl_equity"] < 0 for t in fold_trades) else (None if not any(t["pnl_equity"] > 0 for t in fold_trades) else "INF"),
            "trade_log": fold_trades,
            "cost_stress": fold_cost_stress,
            "funding_boundary_policy_sensitivity": _summarize_fold_funding_boundary(
                rows=rows, end_index=b, start_ts=start_ts, common=fold_common,
                symbol_end_ts=rows[b - 1]["t"], fold_number=fold + 1,
            ),
        })

    funding_quality = summarize_funding_data_quality(funding_rows_for_audit, rows)
    report = {
        "status": "RESEARCH_ONLY",
        "funding_interpretation_gate": (
            "FUNDING_DATA_REVIEW_REQUIRED"
            if funding_quality["review_required"] or funding_quality.get("proxy_mark_price_review_required", False)
            else "TIMESTAMP_ALIGNMENT_ONLY_NOT_RECONCILED"
        ),
        "symbol": args.symbol.upper(),
        "interval": args.interval,
        "interval_note": "CSV timestamps were validated for exact interval continuity; verify source and range before interpreting metrics.",
        "bars": len(rows), "first_ts": rows[0]["t"], "last_ts": rows[-1]["t"],
        "parameters": {key: value for key, value in common.items() if key != "funding_rates"},
        "data_lineage": {
            "candles": source_file_manifest(args.csv),
            "funding": source_file_manifest(args.funding_csv),
            "timestamp_alignment": "exact candle-open timestamp required for funding settlements",
            "event_completeness_verified": False,
            "source_authenticity_verified": False,
            "note": "SHA-256 fingerprints identify the exact local input bytes; they do not authenticate the venue or prove complete coverage.",
        },
        "funding_data": {
            "source": args.funding_csv if historical_funding else None,
            "symbol": historical_funding[0]["symbol"] if historical_funding else None,
            **funding_quality,
        },
        "split": {"train_fraction": args.train_fraction, "boundary_ts": boundary_ts,
                  "warmup_bars": split, "holdout_bars": len(holdout)},
        "funding_boundary_policy_sensitivity": boundary_sensitivity,
        "holdout": {
            "bars": len(holdout), "trades": len(holdout_trades),
            "net_return_pct": round((holdout_result["equity_final"] - 1.0) * 100, 4),
            "max_drawdown_pct": max_drawdown_pct(holdout_curve),
            "equity_final": holdout_result["equity_final"],
            "win_rate_pct": round(sum(p > 0 for p in holdout_pnls) / len(holdout_pnls) * 100, 2) if holdout_pnls else None,
            "profit_factor": round(holdout_profit / holdout_loss, 4) if holdout_loss else (None if not holdout_profit else "INF"),
            "expectancy_equity_pct": round(mean(holdout_pnls) * 100, 4) if holdout_pnls else None,
            "trade_quality_diagnostics": summarize_holdout_trade_quality(holdout_trades),
            "trade_log": holdout_trades,
            "funding_sensitivity": summarize_funding_scenarios({
                "holdout": {
                    "status": holdout_result["status"],
                    "trades": len(holdout_trades),
                    "net_return_pct": round((holdout_result["equity_final"] - 1.0) * 100, 4),
                    "max_drawdown_pct": max_drawdown_pct(holdout_curve),
                    "profit_factor": round(holdout_profit / holdout_loss, 4) if holdout_loss else (None if not holdout_profit else "INF"),
                    "equity_floor_hit": holdout_result.get("equity_floor_hit", False),
                    "trade_log": holdout_trades,
                }
            }),
        },
        "walk_forward": walk_results,
        "walk_forward_cost_stress_summary": summarize_walk_forward_cost_stress(walk_results),
        "cost_stress": cost_stress,
        "warnings": [
            "This runner evaluates a fixed EMA baseline; it does not optimize or certify a profitable model.",
            "Net return is calculated from final account equity relative to the 1.0 starting equity; trade PnL sums are not used as a substitute for compounded portfolio return.",
            ("Historical funding CSV was applied at matching candle timestamps." if historical_funding is not None else "Funding is a constant per-bar assumption, not historical realized funding; provide --funding-csv before production decisions."),
            "All report results require independent review of CSV coverage, exchange data and execution assumptions."
        ],
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"status=RESEARCH_ONLY symbol={args.symbol.upper()} bars={len(rows)} "
          f"holdout_trades={len(holdout_trades)} output={output}")


if __name__ == "__main__":
    main()
