"""Conservative recovery-window planning for Pionex read-only history polling.

Pure computation only: no exchange requests, automatic order placement, or
assumption that the exchange supports these filter names. The API adapter must
map these bounds to verified exchange-specific semantics.
"""


class RecoveryWindowError(ValueError):
    pass


def recovery_window(last_verified_ms, now_ms, *, overlap_ms=300000,
                    max_span_ms=3600000):
    """Return a bounded inclusive UTC millisecond window.

    The overlap intentionally re-reads previously verified fills. Consumers
    MUST deduplicate by immutable exchange fill ID, and MUST NOT advance the
    durable watermark until the whole requested interval is verified.
    """
    if (type(now_ms) is not int or now_ms <= 0
            or type(overlap_ms) is not int or overlap_ms < 0
            or type(max_span_ms) is not int or max_span_ms <= 0):
        raise RecoveryWindowError("Invalid recovery bounds")
    if last_verified_ms is None:
        raise RecoveryWindowError("First-run baseline requires explicit verified backfill")
    if type(last_verified_ms) is not int or last_verified_ms <= 0:
        raise RecoveryWindowError("Invalid checkpoint")
    if last_verified_ms > now_ms:
        raise RecoveryWindowError("Checkpoint is in the future")
    start_ms = max(1, last_verified_ms - overlap_ms)
    # A bounded recovery poll must make forward progress even with overlap.
    # Otherwise a short max_span can loop forever without reaching new fills.
    if start_ms + max_span_ms < last_verified_ms:
        raise RecoveryWindowError("Recovery span cannot reach checkpoint")
    if start_ms + max_span_ms == last_verified_ms and last_verified_ms < now_ms:
        raise RecoveryWindowError("Recovery span cannot advance beyond checkpoint")
    end_ms = min(now_ms, start_ms + max_span_ms)
    if end_ms < start_ms:
        raise RecoveryWindowError("Recovery interval is invalid")
    return {
        "start_ms": start_ms,
        "end_ms": end_ms,
        "more_history_pending": end_ms < now_ms,
        "safe_to_advance": False,
    }
