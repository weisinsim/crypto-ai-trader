"""Pure, read-only readiness assessment for a production account snapshot.

This module evaluates evidence only. It never connects to an exchange or places
orders. Missing or contradictory evidence always blocks readiness.
"""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class ReadinessLimits:
    minimum_available_balance_usdt: float = 100.0
    maximum_open_positions: int = 0
    maximum_gross_notional_usdt: float = 0.0


def assess_production_readiness(snapshot, symbol_rules, limits=ReadinessLimits(),
                                explicit_operator_approval=False):
    """Return blockers for a read-only snapshot; never authorize order execution."""
    blockers = []
    if not isinstance(snapshot, dict) or not snapshot.get("read_only") is True:
        return {"ready": False, "blockers": ["read_only_snapshot_required"],
                "order_submission_authorized": False}
    if snapshot.get("order_submission_supported") is not False:
        blockers.append("snapshot_must_not_claim_order_submission_support")
    if snapshot.get("account_permissions") is not True:
        blockers.append("account_trade_permission_not_confirmed")
    for key in ("available_balance_usdt", "total_wallet_balance_usdt",
                "gross_open_notional_usdt"):
        value = snapshot.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            blockers.append("invalid_" + key)
    if not blockers:
        if snapshot["available_balance_usdt"] < limits.minimum_available_balance_usdt:
            blockers.append("available_balance_below_minimum")
        if snapshot["total_wallet_balance_usdt"] < 0:
            blockers.append("negative_wallet_balance")
        if snapshot["gross_open_notional_usdt"] > limits.maximum_gross_notional_usdt:
            blockers.append("gross_notional_above_readiness_limit")
    count = snapshot.get("open_position_count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        blockers.append("invalid_open_position_count")
    elif count > limits.maximum_open_positions:
        blockers.append("open_positions_must_be_reconciled")
    if not isinstance(snapshot.get("open_positions"), list):
        blockers.append("open_position_details_missing")
    if not isinstance(snapshot.get("position_mode"), dict) or not isinstance(
        snapshot["position_mode"].get("dualSidePosition"), bool
    ):
        blockers.append("position_mode_not_confirmed")
    if not isinstance(symbol_rules, dict):
        blockers.append("symbol_rules_missing")
    else:
        if symbol_rules.get("status") != "TRADING":
            blockers.append("symbol_not_trading")
        if symbol_rules.get("contract_type") != "PERPETUAL":
            blockers.append("symbol_not_perpetual")
        if symbol_rules.get("quote_asset") != "USDT":
            blockers.append("symbol_quote_asset_not_usdt")
        filters = symbol_rules.get("filters")
        if not isinstance(filters, list) or not filters:
            blockers.append("symbol_filters_missing")
    if not explicit_operator_approval:
        blockers.append("explicit_operator_approval_missing")
    # This function is deliberately an assessment, not an execution authorization.
    return {
        "ready": not blockers,
        "blockers": sorted(set(blockers)),
        "order_submission_authorized": False,
        "assessment_only": True,
    }
