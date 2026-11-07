"""Auditable summaries for assumption-based paper simulations.

Simulation rows are never passed to score calibration as verified outcomes. This
module reports completed simulated cohorts separately from right-censored rows.
It performs no I/O, exchange access, persistence, or trading authorization.
"""
from __future__ import annotations

import math
from collections import defaultdict


def _finite(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{field} must be a finite number")
    return float(value)


def _drawdown(returns):
    equity = peak = 1.0
    worst = 0.0
    for ret in returns:
        equity *= 1.0 + ret
        peak = max(peak, equity)
        if peak > 0:
            worst = max(worst, (peak - equity) / peak)
    return worst


def _summarize(rows):
    ordered = sorted(rows, key=lambda r: (r["exit_timestamp_ms"], r["trade_id"]))
    returns = [r["net_return"] for r in ordered]
    wins = sum(value > 0 for value in returns)
    profits = sum(value for value in returns if value > 0)
    losses = -sum(value for value in returns if value < 0)
    return {
        "trade_count": len(ordered),
        "win_count": wins,
        "win_rate": wins / len(ordered) if ordered else None,
        "mean_net_return": sum(returns) / len(returns) if returns else None,
        "profit_factor": profits / losses if losses else (None if profits == 0 else "infinity"),
        "max_drawdown": _drawdown(returns) if returns else None,
        "net_return_sum_non_compounded": sum(returns),
    }


def build_paper_backtest_report(simulated_trades, *, observations=None):
    """Build a transparent simulation report.

    Required trade fields: trade_id, symbol, side, score, status,
    outcome_complete, net_return, exit_timestamp_ms, costs_verified,
    fill_prices_verified. CENSORED/DATA_END rows are coverage only. Inputs must
    be completed simulator outputs enriched with a stable ID and score; this
    report does not establish holdout purity or independently verify costs.
    """
    if not isinstance(simulated_trades, (list, tuple)):
        raise ValueError("simulated_trades must be a list or tuple")
    if observations is not None and not isinstance(observations, (list, tuple)):
        raise ValueError("observations must be a list or tuple when supplied")

    seen = set()
    completed = []
    censored = []
    rejected = []
    for index, raw in enumerate(simulated_trades):
        if not isinstance(raw, dict):
            raise ValueError(f"trade {index} must be a mapping")
        trade_id = raw.get("trade_id")
        if not isinstance(trade_id, str) or not trade_id.strip():
            raise ValueError(f"trade {index} requires a non-empty trade_id")
        trade_id = trade_id.strip()
        if trade_id in seen:
            raise ValueError(f"duplicate trade_id: {trade_id}")
        seen.add(trade_id)
        symbol = raw.get("symbol")
        side = raw.get("side")
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError(f"trade {trade_id} has invalid symbol")
        if side not in ("LONG", "SHORT"):
            raise ValueError(f"trade {trade_id} side must be LONG or SHORT")
        score = _finite(raw.get("score"), f"{trade_id}.score")
        if not 0 <= score <= 100:
            raise ValueError(f"trade {trade_id} score must be in [0, 100]")
        exit_ts = _finite(raw.get("exit_timestamp_ms"), f"{trade_id}.exit_timestamp_ms")
        if exit_ts < 0:
            raise ValueError(f"trade {trade_id} exit timestamp must be nonnegative")
        status = raw.get("status")
        complete = raw.get("outcome_complete")
        if not isinstance(complete, bool):
            raise ValueError(f"trade {trade_id} outcome_complete must be boolean")
        if not isinstance(raw.get("costs_verified"), bool) or not isinstance(raw.get("fill_prices_verified"), bool):
            raise ValueError(f"trade {trade_id} verification flags must be booleans")

        normalized = {
            "trade_id": trade_id, "symbol": symbol.strip().upper(), "side": side,
            "score": score, "exit_timestamp_ms": exit_ts,
        }
        if status == "CENSORED" or raw.get("exit_reason") == "DATA_END":
            if complete:
                raise ValueError(f"trade {trade_id} is censored but marked outcome_complete=True")
            censored.append(normalized)
            continue
        if status != "SIMULATED" or not complete:
            rejected.append({"trade_id": trade_id, "reason": "NOT_A_COMPLETED_SIMULATION"})
            continue
        net_return = _finite(raw.get("net_return"), f"{trade_id}.net_return")
        if net_return <= -1:
            raise ValueError(f"trade {trade_id} net_return must be greater than -1")
        # Flags must be preserved as evidence; even True does not prove the
        # report input is a genuine exchange fill or a sealed holdout sample.
        normalized.update({
            "net_return": net_return,
            "costs_verified": raw["costs_verified"],
            "fill_prices_verified": raw["fill_prices_verified"],
        })
        completed.append(normalized)

    groups = defaultdict(list)
    for row in completed:
        groups[(row["symbol"], row["side"])].append(row)
    by_symbol_side = []
    for key in sorted(groups):
        summary = _summarize(groups[key])
        summary.update({"symbol": key[0], "side": key[1]})
        by_symbol_side.append(summary)

    score_bands = ((0.0, 50.0), (50.0, 60.0), (60.0, 70.0),
                   (70.0, 80.0), (80.0, 90.0), (90.0, 100.0000001))
    by_score_band = []
    for lower, upper in score_bands:
        rows = [r for r in completed if lower <= r["score"] < upper]
        if not rows:
            continue
        summary = _summarize(rows)
        summary.update({"score_band": f"{lower:g}-{min(upper, 100):g}",
                        "mean_score": sum(r["score"] for r in rows) / len(rows)})
        by_score_band.append(summary)

    return {
        "status": "RESEARCH_ONLY_SIMULATION",
        "input_trade_count": len(simulated_trades),
        "completed_simulation_count": len(completed),
        "right_censored_count": len(censored),
        "rejected_count": len(rejected),
        "completed_simulations": _summarize(completed),
        "by_symbol_side": by_symbol_side,
        "by_score_band": by_score_band,
        "censored_trade_ids": [row["trade_id"] for row in censored],
        "rejected": rejected,
        "costs_verified_for_all_completed": bool(completed) and all(r["costs_verified"] for r in completed),
        "fill_prices_verified_for_all_completed": bool(completed) and all(r["fill_prices_verified"] for r in completed),
        "simulation_only": True,
        "costs_verified": False,
        "fills_verified": False,
        "score_is_probability": False,
        "out_of_sample_verified": False,
        "verified_score_calibration": None,
        "observations_count": len(observations) if observations is not None else None,
        "warning": "Assumption-based simulated outcomes only. Censored outcomes are excluded from performance metrics. This report does not verify costs, fills, holdout purity, or profitability; score is not a win-probability estimate.",
        "auto_trade_authorized": False,
    }
