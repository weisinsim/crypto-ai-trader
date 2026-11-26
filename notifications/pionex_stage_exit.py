"""Offline safe exit staging boundary for normalized Pionex evidence.

No network calls, live notifications or order placement. The watermark must
NOT be advanced by this function; that requires a separately verified
complete time interval and crash-consistent transaction design.
"""
from notifications.pionex_exit_assembly import assemble_exit_evidence


def stage_verified_exit(store, before, after, pages, orders, **verification):
    """Stage only an assembled event; repeated identical evidence is idempotent.

    Returns (event, newly_staged). An unchanged position returns (None, False).
    Raises on incomplete/contradictory evidence; the store remains untouched
    unless the entire assembly succeeds.
    """
    event = assemble_exit_evidence(
        before, after, pages, orders, **verification)
    if event is None:
        return None, False
    return event, store.stage(event)
