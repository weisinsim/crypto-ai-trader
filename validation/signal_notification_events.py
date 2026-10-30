"""Pure builders for signal-only notification events."""
from __future__ import annotations

EVENT_SUFFIXES = {
    "ENTRY_CONFIRMED": "ENTRY",
    "TAKE_PROFIT_1": "TP1",
    "TAKE_PROFIT_2": "TP2",
    "STOP_LOSS": "STOP_LOSS",
    "SIGNAL_INVALIDATED": "SIGNAL_INVALIDATED",
}


def build_signal_notification_event(event: str, *, symbol: str, direction: str,
                                    signal_time: int, price=None, entry=None,
                                    sl=None, tp1=None, tp2=None, level=None,
                                    score=None) -> dict:
    """Build a stable, minimal event payload; no execution or account state implied."""
    if event not in EVENT_SUFFIXES:
        raise ValueError("unsupported_signal_notification_event")
    if not isinstance(symbol, str) or not symbol or not symbol.isascii():
        raise ValueError("invalid_signal_notification_symbol")
    if direction not in ("LONG", "SHORT"):
        raise ValueError("invalid_signal_notification_direction")
    if isinstance(signal_time, bool) or not isinstance(signal_time, int) or signal_time <= 0:
        raise ValueError("invalid_signal_notification_time")
    payload = {
        "event_id": f"{symbol}:{signal_time}:{EVENT_SUFFIXES[event]}",
        "event": event,
        "symbol": symbol,
        "direction": direction,
        "signal_time": signal_time,
    }
    optional = {"price": price, "entry": entry, "sl": sl, "tp1": tp1,
                "tp2": tp2, "level": level, "score": score}
    payload.update({key: value for key, value in optional.items() if value is not None})
    return payload
