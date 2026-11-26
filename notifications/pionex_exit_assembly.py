"""Offline assembly of Pionex perpetual exit evidence.

Only produces a non-deliverable event for durable staging. Real Pionex
response normalization and exchange semantics are NOT implemented here.
"""
from __future__ import annotations

from notifications.pionex_exit_reason import classify_exit_reason
from notifications.pionex_fill_pages import collect_verified_fill_pages
from notifications.pionex_reconcile import reconcile_reduction


class ExitEvidenceError(ValueError):
    pass


def assemble_exit_evidence(before, after, pages, orders, *,
                           account_scope, position_epoch, expected_symbol,
                           pages_complete=False, same_account=False,
                           order_schema_verified=False,
                           all_fills_matched=False,
                           order_coverage_complete=False):
    """Require all evidence before assembling a non-deliverable exit record.

    Never convert an exception or incomplete history to a positive exit.
    The caller must independently establish each evidence flag from actual
    exchange data; caller flags alone are NOT production proof.
    """
    fills = collect_verified_fill_pages(
        pages, expected_symbol=expected_symbol, complete=pages_complete)
    if (before.get("symbol") != expected_symbol
            or after.get("symbol") != expected_symbol):
        raise ExitEvidenceError("Position and fill symbol mismatch")
    event = reconcile_reduction(
        before, after, fills, coverage_complete=pages_complete,
        same_account=same_account, account_scope=account_scope,
        position_epoch=position_epoch)
    if event is None:
        return None
    matched_ids = {str(fill["order_id"]) for fill in fills}
    supplied_ids = {str(order.get("order_id")) for order in orders} if isinstance(orders, list) else set()
    if not matched_ids or matched_ids != supplied_ids:
        raise ExitEvidenceError("Closing order IDs do not match fill evidence")
    reason = classify_exit_reason(
        orders, schema_verified=order_schema_verified,
        all_fills_matched=all_fills_matched,
        coverage_complete=order_coverage_complete)
    event["classification"] = reason
    event["notification_ready"] = False
    return event
