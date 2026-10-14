"""Fail-closed paper-portfolio consistency check before order-gate evaluation."""
from __future__ import annotations

from collections.abc import Mapping

from validation.guarded_order_gate import evaluate_order_gate_with_daily_loss_guard
from validation.paper_reconciliation import reconcile_paper_positions


def evaluate_order_gate_with_paper_portfolio(
    snapshot, limits, daily_loss_guard, utc_date, paper_orders
):
    """Evaluate existing gates only when the supplied position count matches paper ledger.

    Counts active symbols by gross filled exposure, not net exposure, so opposing
    long/short legs cannot make a live risk position disappear through netting.
    This is paper-only decision logic and never submits orders.
    """
    reasons = []
    try:
        positions = reconcile_paper_positions(paper_orders)
    except (TypeError, ValueError) as exc:
        return {
            "approved": False,
            "reasons": ["PAPER_PORTFOLIO_INVALID"],
            "portfolio_error": str(exc),
        }

    expected_open_positions = sum(
        1 for item in positions.values() if item["open_quantity"] > 0
    )
    if not isinstance(snapshot, Mapping):
        return {
            "approved": False,
            "reasons": ["INVALID_SNAPSHOT"],
            "portfolio_open_positions": expected_open_positions,
        }
    supplied = snapshot.get("open_positions")
    if (
        isinstance(supplied, bool)
        or not isinstance(supplied, (int, float))
        or supplied != expected_open_positions
    ):
        reasons.append("PAPER_PORTFOLIO_POSITION_COUNT_MISMATCH")

    decision = evaluate_order_gate_with_daily_loss_guard(
        snapshot, limits, daily_loss_guard, utc_date
    )
    reasons.extend(decision["reasons"])
    return {
        "approved": decision["approved"] and not reasons,
        "reasons": list(dict.fromkeys(reasons)),
        "daily_loss_status": decision["daily_loss_status"],
        "portfolio_open_positions": expected_open_positions,
        "portfolio_symbols": positions,
    }
