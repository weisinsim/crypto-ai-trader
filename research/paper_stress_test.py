"""Deterministic paper-simulation stress scenarios; never places orders.

Each scenario replays the same frozen candidates with explicit cost assumptions.
Outputs are sensitivity analysis, not verified exchange performance or a tuning
oracle. Right-censored rows remain excluded from performance metrics.
"""
from __future__ import annotations

import math

from research.paper_trade_simulator import simulate_tactical_candidate
from research.paper_backtest_report import build_paper_backtest_report


DEFAULT_SCENARIOS = (
    {"scenario_id": "base", "fee_bps_per_side": 5.0, "slippage_bps_per_side": 2.0, "funding_bps_per_8h": 0.0},
    {"scenario_id": "moderate_cost", "fee_bps_per_side": 6.0, "slippage_bps_per_side": 5.0, "funding_bps_per_8h": 2.0},
    {"scenario_id": "high_cost", "fee_bps_per_side": 8.0, "slippage_bps_per_side": 10.0, "funding_bps_per_8h": 5.0},
)


def run_paper_stress_scenarios(candidates, *, scenarios=DEFAULT_SCENARIOS,
                               max_holding_candles=24):
    """Replay candidates under cost scenarios and report each scenario separately.

    Candidate schema: trade_id, score, observation, future_candles. All scenario
    settings must be explicit and nonnegative. This function assumes input
    candles are already frozen and chronologically validated by the caller.
    """
    if not isinstance(candidates, (list, tuple)):
        raise ValueError("candidates must be a list or tuple")
    if not isinstance(scenarios, (list, tuple)) or not scenarios:
        raise ValueError("scenarios must be a non-empty list or tuple")
    seen_candidates = set()
    for i, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            raise ValueError(f"candidate {i} must be a mapping")
        trade_id = candidate.get("trade_id")
        if not isinstance(trade_id, str) or not trade_id.strip():
            raise ValueError(f"candidate {i} requires a non-empty trade_id")
        trade_id = trade_id.strip()
        if trade_id in seen_candidates:
            raise ValueError(f"duplicate candidate trade_id: {trade_id}")
        seen_candidates.add(trade_id)
        score = candidate.get("score")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 100:
            raise ValueError(f"candidate {trade_id} score must be finite and in [0, 100]")
        if not isinstance(candidate.get("observation"), dict):
            raise ValueError(f"candidate {trade_id} requires observation mapping")
        if not isinstance(candidate.get("future_candles"), (list, tuple)):
            raise ValueError(f"candidate {trade_id} requires future_candles list or tuple")

    scenario_ids = set()
    reports = []
    for index, scenario in enumerate(scenarios):
        if not isinstance(scenario, dict):
            raise ValueError(f"scenario {index} must be a mapping")
        scenario_id = scenario.get("scenario_id")
        if not isinstance(scenario_id, str) or not scenario_id.strip():
            raise ValueError(f"scenario {index} requires a non-empty scenario_id")
        scenario_id = scenario_id.strip()
        if scenario_id in scenario_ids:
            raise ValueError(f"duplicate scenario_id: {scenario_id}")
        scenario_ids.add(scenario_id)
        settings = {}
        for field, default in (("fee_bps_per_side", 5.0),
                               ("slippage_bps_per_side", 2.0),
                               ("funding_bps_per_8h", 0.0)):
            value = scenario.get(field, default)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"scenario {scenario_id} {field} must be finite and nonnegative")
            settings[field] = float(value)

        rows = []
        filtered_candidates = []
        for candidate in candidates:
            observation = candidate["observation"]
            candles = candidate["future_candles"]
            outcome = simulate_tactical_candidate(
                observation=observation, future_candles=candles,
                max_holding_candles=max_holding_candles, **settings)
            if outcome.get("status") == "NO_TRADE":
                filtered_candidates.append({
                    "trade_id": candidate["trade_id"].strip(),
                    "status": "NO_TRADE",
                    "reason": outcome.get("reason", "NON_ACTIONABLE_SIGNAL"),
                })
                continue
            bars_held = outcome["bars_held"]
            exit_index = min(bars_held, len(candles)) - 1
            exit_ts = candles[exit_index]["t"]
            rows.append({
                "trade_id": candidate["trade_id"].strip(),
                "symbol": outcome["symbol"],
                "side": outcome["side"],
                "score": float(candidate["score"]),
                "status": outcome["status"],
                "outcome_complete": outcome["outcome_complete"],
                "net_return": outcome["net_return_on_entry_notional"],
                "exit_timestamp_ms": exit_ts,
                "exit_reason": outcome["exit_reason"],
                "funding_model": outcome.get("funding_model", "UNSPECIFIED"),
                "funding_history_verified": outcome.get("funding_history_verified", False),
                "funding_intervals": outcome.get("funding_intervals"),
                "costs_verified": False,
                "fill_prices_verified": False,
            })
        report = build_paper_backtest_report(rows)
        completed_count = report["completed_simulation_count"]
        censored_count = report["right_censored_count"]
        rejected_count = report["rejected_count"]
        filtered_count = len(filtered_candidates)
        # Every supplied candidate must be visible in exactly one coverage bucket.
        classified_count = completed_count + censored_count + rejected_count + filtered_count
        if classified_count != len(candidates):
            raise RuntimeError(
                "candidate coverage invariant failed: "
                f"{classified_count} classified vs {len(candidates)} supplied"
            )
        reports.append({
            "scenario_id": scenario_id,
            "assumptions": settings,
            "funding_model": "CANDLE_BUCKET_ASSUMPTION_NOT_EXCHANGE_SETTLEMENT",
            "funding_history_verified": False,
            "report": report,
            "coverage": {
                "input_candidate_count": len(candidates),
                "completed_count": completed_count,
                "right_censored_count": censored_count,
                "rejected_count": rejected_count,
                "filtered_count": filtered_count,
                "classified_count": classified_count,
                "coverage_complete": classified_count == len(candidates),
            },
            "filtered_candidate_count": filtered_count,
            "filtered_candidates": filtered_candidates,
            "simulated_candidate_count": len(rows),
            "simulation_only": True,
            "costs_verified": False,
            "fills_verified": False,
            "auto_trade_authorized": False,
        })

    return {
        "status": "RESEARCH_ONLY_STRESS_ANALYSIS",
        "candidate_count": len(candidates),
        "scenario_count": len(reports),
        "scenarios": reports,
        "historical_funding_verified": False,
        "realistic_fill_model_verified": False,
        "verified_score_calibration": None,
        "warning": "Cost sensitivity only. Scenarios are assumptions, not measured execution costs. Do not select a model solely because it performs best in this grid; validate on an untouched holdout.",
        "auto_trade_authorized": False,
    }
