"""Pure helpers for dashboard signal confirmation state."""


def invalidate_stale_confirmation(previous_signal, new_signal, cache_entry, history, timestamp_ms):
    """Clear a prior confirmed direction when the current closed-candle signal changes.

    The caller must apply its model-validation gate after this function. This ensures
    an unapproved opposite signal cannot leave the previous direction marked ACTIVE.
    """
    if previous_signal not in ("LONG", "SHORT") or new_signal == previous_signal:
        return previous_signal

    if history:
        history[0]["status"] = "INVALIDATED"
        history[0]["ended_at"] = timestamp_ms

    cache_entry.update({
        "confirmed_signal": "NO-TRADE",
        "confirmed_signal_time": timestamp_ms,
        "confirmed_entry": None,
        "confirmed_sl": None,
        "confirmed_tp1": None,
        "confirmed_tp2": None,
        "confirmed_status": "INVALIDATED",
    })
    return "NO-TRADE"
