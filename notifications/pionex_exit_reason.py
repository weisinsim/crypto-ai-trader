"""Conservative classification of Pionex perpetual exit causes.

Inputs are normalized, independently verified *executed* closing orders.
Do not infer TP/SL from prices or order direction. The live Pionex adapter
must verify exchange order-type values and trigger execution semantics first.
This module never contacts an exchange or sends notifications.
"""


class ExitReasonError(ValueError):
    pass


def classify_exit_reason(orders, *, schema_verified=False,
                         all_fills_matched=False, coverage_complete=False):
    """Classify only homogeneous fully executed close orders.

    Each order is normalized to {order_id, close_effect, execution_status,
    trigger_kind}. trigger_kind must be one of TAKE_PROFIT, STOP_LOSS,
    MANUAL, OTHER or UNKNOWN, as proven by exchange order details.
    """
    if (schema_verified is not True or all_fills_matched is not True
            or coverage_complete is not True):
        raise ExitReasonError("Unverified exchange order evidence")
    if not isinstance(orders, list) or not orders:
        raise ExitReasonError("Missing closing orders")
    seen = set()
    reasons = set()
    for order in orders:
        if not isinstance(order, dict) or set(order) != {
            "order_id", "close_effect", "execution_status", "trigger_kind"
        }:
            raise ExitReasonError("Invalid normalized order")
        oid = order["order_id"]
        if (not isinstance(oid, (str, int)) or isinstance(oid, bool)
                or not str(oid) or str(oid) in seen):
            raise ExitReasonError("Missing or duplicate order ID")
        seen.add(str(oid))
        if order["close_effect"] != "CLOSE" or order["execution_status"] != "EXECUTED":
            raise ExitReasonError("Order does not establish executed close")
        reason = order["trigger_kind"]
        if reason not in {"TAKE_PROFIT", "STOP_LOSS", "MANUAL", "OTHER", "UNKNOWN"}:
            raise ExitReasonError("Unsupported trigger kind")
        reasons.add(reason)
    if len(reasons) != 1 or "UNKNOWN" in reasons:
        return "EXIT_REASON_UNVERIFIED"
    return next(iter(reasons))
