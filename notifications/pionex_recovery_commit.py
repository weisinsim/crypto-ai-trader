"""Fail-closed boundary between a recovery window and atomic checkpoint.

Offline orchestration only. This cannot establish real exchange coverage,
validate position evidence, send alerts, or execute trades. Callers must
independently verify every evidence gate using authoritative exchange data.
"""
from notifications.pionex_recovery_window import recovery_window, RecoveryWindowError


class RecoveryCommitError(ValueError):
    pass


def commit_recovered_window(store, account_scope, symbol, now_ms, *,
                            event=None, overlap_ms=300000, max_span_ms=3600000,
                            pages_complete=False, positions_verified=False,
                            fills_reconciled=False, order_evidence_verified=False):
    """Commit one bounded interval only after independently verified gates.

    Never bootstrap a missing checkpoint or silently skip a time gap.
    Returns the window and the atomic commit result; no notifications.
    """
    previous = store.checkpoint(account_scope, symbol)
    if previous is None:
        raise RecoveryCommitError("Explicit verified first-run baseline required")
    try:
        window = recovery_window(previous, now_ms,
                                 overlap_ms=overlap_ms, max_span_ms=max_span_ms)
    except RecoveryWindowError as exc:
        raise RecoveryCommitError("Invalid recovery interval") from exc
    if (window["start_ms"] > previous or window["end_ms"] < previous
            or pages_complete is not True or positions_verified is not True
            or fills_reconciled is not True or order_evidence_verified is not True):
        raise RecoveryCommitError("Recovery coverage or evidence incomplete")
    result = store.commit_verified_interval(
        account_scope, symbol, window["end_ms"], event,
        pages_complete=True, positions_verified=True,
        fills_reconciled=True, order_evidence_verified=True)
    return {"window": window, "commit": result}
