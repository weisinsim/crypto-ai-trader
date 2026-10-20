"""Guarded TESTNET entry workflow; not production/live trading.

Runs risk gates before submitting one explicitly identified market order, then
reconciles the order and position and attempts protective exits for any exposure.
Network ambiguity returns a reconciliation-required state and never auto-retries.
"""
from decimal import Decimal, InvalidOperation

from execution.runtime_guard import RuntimeRiskConfig, RuntimeSnapshot, validate_runtime_state
from execution.safety import ExecutionConfig, OrderIntent, validate_order_intent
from execution.testnet_lifecycle import ensure_testnet_protective_exits


def execute_guarded_testnet_entry(
    adapter,
    intent: OrderIntent,
    client_order_id: str,
    client_order_prefix: str,
    runtime_snapshot: RuntimeSnapshot,
    runtime_config: RuntimeRiskConfig = RuntimeRiskConfig(),
    execution_config: ExecutionConfig = ExecutionConfig(mode="testnet"),
):
    """Attempt a single guarded TESTNET entry; caller must supply fresh reconciled state."""
    if execution_config.mode != "testnet" or execution_config.live_trading_enabled:
        return {"ok": False, "reason": "testnet_mode_required"}
    if not isinstance(client_order_id, str) or not client_order_id or len(client_order_id) > 36:
        return {"ok": False, "reason": "invalid_client_order_id"}
    if not client_order_id.replace("-", "").replace("_", "").isalnum():
        return {"ok": False, "reason": "invalid_client_order_id"}
    if not isinstance(client_order_prefix, str) or not client_order_prefix or len(client_order_prefix) > 20:
        return {"ok": False, "reason": "invalid_client_order_prefix"}
    if not client_order_prefix.replace("-", "").replace("_", "").isalnum():
        return {"ok": False, "reason": "invalid_client_order_prefix"}

    runtime_decision = validate_runtime_state(runtime_snapshot, runtime_config)
    if not runtime_decision.allowed:
        return {"ok": False, "reason": runtime_decision.reason, "stage": "runtime_gate"}
    intent_decision = validate_order_intent(intent, execution_config)
    if not intent_decision.allowed:
        return {"ok": False, "reason": intent_decision.reason, "stage": "intent_gate"}

    try:
        preflight = adapter.account_preflight()
    except Exception as exc:
        return {
            "ok": False, "reason": "testnet_account_preflight_error",
            "error_type": type(exc).__name__, "stage": "account_preflight",
        }
    if not isinstance(preflight, dict) or not preflight.get("ok") or not preflight.get("can_trade"):
        return {"ok": False, "reason": "testnet_account_preflight_failed", "detail": preflight}

    # Never retry this call automatically: an HTTP timeout may occur after acceptance.
    submission = None
    submission_error = None
    try:
        submission = adapter.submit_testnet_market_order(intent, client_order_id, execution_config)
    except Exception as exc:
        submission_error = type(exc).__name__
    if submission is not None and (not isinstance(submission, dict) or not submission.get("accepted")):
        return {"ok": False, "reason": "market_order_rejected", "detail": submission}

    # Even after a timeout, reconcile by the same client ID; never issue a second POST.
    try:
        order = adapter.query_testnet_order(intent.symbol, client_order_id)
        snapshot = adapter.testnet_position_snapshot(intent.symbol)
    except Exception as exc:
        return {
            "ok": False,
            "reason": "market_order_outcome_ambiguous" if submission_error else "post_entry_reconciliation_failed",
            "error_type": submission_error or type(exc).__name__,
            "client_order_id": client_order_id,
            "requires_reconciliation": True,
        }
    if not isinstance(order, dict) or not order.get("found") or not isinstance(snapshot, dict) or not snapshot.get("ok"):
        return {
            "ok": False,
            "reason": "market_order_outcome_ambiguous" if submission_error else "post_entry_reconciliation_incomplete",
            "submission_error": submission_error,
            "order": order,
            "snapshot": snapshot,
            "client_order_id": client_order_id,
            "requires_reconciliation": True,
        }
    try:
        position = Decimal(str(snapshot["position_amount"]))
        entry = Decimal(str(snapshot["entry_price"]))
        executed = Decimal(str(order.get("executed_quantity", "0")))
        if not all(v.is_finite() for v in (position, entry, executed)):
            raise InvalidOperation
    except (InvalidOperation, TypeError, ValueError, KeyError):
        return {
            "ok": False,
            "reason": "post_entry_values_invalid",
            "order": order,
            "requires_reconciliation": True,
        }

    if position != 0 and (
        order.get("status") not in {"FILLED", "PARTIALLY_FILLED"}
        or executed <= 0
        or executed != abs(position)
    ):
        return {
            "ok": False,
            "reason": "order_fill_and_position_quantity_mismatch",
            "order_status": order.get("status"),
            "executed_quantity": str(executed),
            "position_amount": str(position),
            "requires_reconciliation": True,
        }

    if position == 0:
        terminal_no_fill = order.get("status") in {"CANCELED", "EXPIRED", "REJECTED"} and executed == 0
        return {
            "ok": bool(terminal_no_fill and submission_error is None),
            "reason": "no_open_position_after_entry" if terminal_no_fill else "order_state_requires_reconciliation",
            "order_status": order.get("status"),
            "executed_quantity": str(executed),
            "requires_reconciliation": not terminal_no_fill or submission_error is not None,
        }
    if entry <= 0 or (position > 0) != (intent.side == "BUY"):
        return {
            "ok": False,
            "reason": "actual_position_does_not_match_requested_direction",
            "position_amount": str(position),
            "requires_reconciliation": True,
        }

    stop = Decimal(str(intent.stop_loss))
    target = Decimal(str(intent.take_profit))
    if (position > 0 and not stop < entry < target) or (position < 0 and not target < entry < stop):
        return {
            "ok": False,
            "reason": "actual_fill_entry_invalidates_protective_geometry",
            "position_amount": str(position),
            "actual_entry": str(entry),
            "requires_reconciliation": True,
        }

    protection = ensure_testnet_protective_exits(
        adapter,
        intent.symbol,
        stop,
        target,
        client_order_prefix,
    )
    if not protection.get("ok"):
        return {
            "ok": False,
            "reason": "position_open_but_protective_exits_not_verified",
            "order": order,
            "position_amount": str(position),
            "protection": protection,
            "requires_reconciliation": True,
        }
    return {
        "ok": True,
        "mode": "guarded_testnet_entry_with_verified_protection",
        "symbol": intent.symbol,
        "order": order,
        "position_amount": str(position),
        "actual_entry": str(entry),
        "protection": protection,
        "live_trading_enabled": False,
        "submission_outcome_was_ambiguous": submission_error is not None,
        "note": "Single-pass reconciliation only; partial fills, races, and process restarts require a persistent supervisor.",
    }
