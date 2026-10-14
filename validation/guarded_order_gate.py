"""Compose the existing fail-closed gate with the persistent daily-loss lockout."""
from __future__ import annotations

from validation.promotion_gate import evaluate_live_order_gate


def evaluate_order_gate_with_daily_loss_guard(snapshot, limits, daily_loss_guard, utc_date):
    """Reject entries when the UTC-day loss guard is locked; never submits an order.

    This is a decision helper for tests/research. The caller remains responsible for
    wiring it correctly and for preventing all execution paths from bypassing it.
    """
    daily_status = daily_loss_guard.status(utc_date)
    decision = evaluate_live_order_gate(snapshot, limits)
    reasons = list(decision["reasons"])
    if daily_status["locked"]:
        reasons.append("PERSISTENT_DAILY_LOSS_LOCKED")
    return {
        "approved": decision["approved"] and not daily_status["locked"],
        "reasons": reasons,
        "daily_loss_status": daily_status,
    }
