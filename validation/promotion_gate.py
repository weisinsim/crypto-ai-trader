#!/usr/bin/env python3
"""Conservative screening helpers; passing is not production approval."""
from collections.abc import Mapping
import math


def _finite_number(value):
    """Validate numeric inputs without accepting booleans or overflowing huge integers."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, ValueError):
        return False


def classify_candidate(dev_net_r, holdout_net_r, holdout_trades, holdout_profit_factor, min_trades=30):
    if (
        isinstance(min_trades, bool)
        or not isinstance(min_trades, int)
        or min_trades < 1
        or isinstance(holdout_trades, bool)
        or not isinstance(holdout_trades, int)
        or holdout_trades < min_trades
    ):
        return "FAIL_INSUFFICIENT_HOLDOUT_TRADES"
    values = (dev_net_r, holdout_net_r)
    if any(not _finite_number(v) for v in values):
        return "FAIL_MISSING_OR_INVALID_NET_R"
    if dev_net_r <= 0 or holdout_net_r <= 0:
        return "FAIL_NONPOSITIVE_DEVELOPMENT_OR_HOLDOUT"
    if (
        not _finite_number(holdout_profit_factor)
        or holdout_profit_factor <= 1
    ):
        return "FAIL_HOLDOUT_PROFIT_FACTOR"
    return "PASS_PRELIMINARY_SCREEN_ONLY"


def evaluate_live_order_gate(snapshot, limits):
    """Fail-closed pre-trade gate. It evaluates a proposal only; it never submits it."""
    if not isinstance(snapshot, Mapping):
        return {"approved": False, "reasons": ["INVALID_SNAPSHOT_TYPE"]}
    if not isinstance(limits, Mapping):
        return {"approved": False, "reasons": ["INVALID_LIMITS_TYPE"]}

    reasons = []
    required_snapshot = (
        "exchange_healthy", "kill_switch_active", "live_execution_enabled", "candle_closed", "model_status",
        "signal", "data_age_sec", "analysis_age_sec", "spread_bps",
        "risk_fraction", "daily_loss_fraction", "open_positions", "requested_notional",
    )
    required_limits = (
        "max_data_age_sec", "max_analysis_age_sec", "max_spread_bps",
        "max_risk_fraction", "max_daily_loss_fraction", "max_open_positions", "max_notional",
    )
    for key in required_snapshot:
        if key not in snapshot:
            reasons.append(f"MISSING_{key.upper()}")
    for key in required_limits:
        if key not in limits:
            reasons.append(f"MISSING_LIMIT_{key.upper()}")
    if reasons:
        return {"approved": False, "reasons": reasons}

    if snapshot["exchange_healthy"] is not True:
        reasons.append("EXCHANGE_UNHEALTHY")
    if snapshot["kill_switch_active"] is not False:
        reasons.append("KILL_SWITCH_ACTIVE")
    if snapshot["live_execution_enabled"] is not True:
        reasons.append("LIVE_EXECUTION_DISABLED")
    if snapshot["candle_closed"] is not True:
        reasons.append("CANDLE_NOT_CLOSED")
    if snapshot["model_status"] != "PRODUCTION":
        reasons.append("MODEL_NOT_PRODUCTION_APPROVED")
    if snapshot["signal"] not in ("LONG", "SHORT"):
        reasons.append("NO_ACTIONABLE_SIGNAL")

    numeric_snapshot = (
        "data_age_sec", "analysis_age_sec", "spread_bps", "risk_fraction",
        "daily_loss_fraction", "open_positions", "requested_notional",
    )
    for key in numeric_snapshot:
        value = snapshot[key]
        if not _finite_number(value):
            reasons.append(f"INVALID_{key.upper()}")
    for key in required_limits:
        value = limits[key]
        if not _finite_number(value):
            reasons.append(f"INVALID_LIMIT_{key.upper()}")
    if any(reason.startswith(("INVALID_", "INVALID_LIMIT_")) for reason in reasons):
        return {"approved": False, "reasons": reasons}

    if any(limits[key] < 0 for key in required_limits):
        reasons.append("INVALID_NEGATIVE_LIMIT")
    if limits["max_open_positions"] < 1 or int(limits["max_open_positions"]) != limits["max_open_positions"]:
        reasons.append("INVALID_MAX_OPEN_POSITIONS")
    if snapshot["open_positions"] < 0 or int(snapshot["open_positions"]) != snapshot["open_positions"]:
        reasons.append("INVALID_OPEN_POSITIONS")
    if not 0 < snapshot["risk_fraction"] <= limits["max_risk_fraction"]:
        reasons.append("RISK_LIMIT_EXCEEDED")
    if snapshot["daily_loss_fraction"] < 0:
        reasons.append("INVALID_DAILY_LOSS_FRACTION")
    elif snapshot["daily_loss_fraction"] >= limits["max_daily_loss_fraction"]:
        reasons.append("DAILY_LOSS_LIMIT_REACHED")
    if snapshot["data_age_sec"] < 0 or snapshot["analysis_age_sec"] < 0 or snapshot["spread_bps"] < 0:
        reasons.append("INVALID_NEGATIVE_MARKET_METRIC")
    if snapshot["data_age_sec"] > limits["max_data_age_sec"]:
        reasons.append("STALE_MARKET_DATA")
    if snapshot["analysis_age_sec"] > limits["max_analysis_age_sec"]:
        reasons.append("STALE_ANALYSIS")
    if snapshot["spread_bps"] > limits["max_spread_bps"]:
        reasons.append("SPREAD_LIMIT_EXCEEDED")
    if snapshot["open_positions"] >= limits["max_open_positions"]:
        reasons.append("OPEN_POSITION_LIMIT_REACHED")
    if snapshot["requested_notional"] <= 0 or snapshot["requested_notional"] > limits["max_notional"]:
        reasons.append("NOTIONAL_LIMIT_EXCEEDED")
    return {"approved": not reasons, "reasons": reasons}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dev-net-r", type=float, required=True)
    parser.add_argument("--holdout-net-r", type=float, required=True)
    parser.add_argument("--holdout-trades", type=int, required=True)
    parser.add_argument("--holdout-profit-factor", type=float, required=True)
    parser.add_argument("--min-trades", type=int, default=30)
    args = parser.parse_args()
    print(classify_candidate(args.dev_net_r, args.holdout_net_r, args.holdout_trades, args.holdout_profit_factor, args.min_trades))
