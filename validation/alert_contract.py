"""Fail-closed validation helpers for read-only dashboard entry alerts.

This module does not place, queue, or authorize exchange orders.
"""
import hashlib
import math

ALLOWED_MODEL_STATUSES = frozenset({"RESEARCH", "PRODUCTION"})


def _finite_number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def validate_alert_candidate(symbol, item, now, max_age_seconds=90 * 60):
    """Return a sanitized notification-only alert, or None when unsafe/stale."""
    if not isinstance(symbol, str) or not symbol.endswith("USDT"):
        return None
    if not isinstance(item, dict) or not _finite_number(now) or now <= 0:
        return None

    side = item.get("confirmed_signal")
    model_status = item.get("model_status")
    if side not in ("LONG", "SHORT") or model_status not in ALLOWED_MODEL_STATUSES:
        return None

    ticker_ts = item.get("ts")
    price = item.get("price")
    if not _finite_number(ticker_ts) or ticker_ts <= 0 or ticker_ts > now or now - ticker_ts > 120:
        return None
    if not _finite_number(price) or price <= 0:
        return None

    candle_ms = item.get("confirmed_signal_time")
    analysis_ts = item.get("analysis_ts")
    if not _finite_number(candle_ms) or candle_ms <= 0 or candle_ms > now * 1000:
        return None
    if now * 1000 - candle_ms > max_age_seconds * 1000:
        return None
    if not _finite_number(analysis_ts) or analysis_ts <= 0 or analysis_ts > now or now - analysis_ts > max_age_seconds:
        return None

    entry, stop, tp1, tp2 = (
        item.get("confirmed_entry"),
        item.get("confirmed_sl"),
        item.get("confirmed_tp1"),
        item.get("confirmed_tp2"),
    )
    if not all(_finite_number(v) and v > 0 for v in (entry, stop, tp1, tp2)):
        return None
    if side == "LONG" and not stop < entry < tp1 <= tp2:
        return None
    if side == "SHORT" and not tp2 <= tp1 < entry < stop:
        return None

    score = item.get("confirmed_score")
    if not _finite_number(score) or not 0 <= score <= 100:
        return None

    event_material = f"{symbol}|{side}|{int(candle_ms)}|{item.get('model_name', '')}"
    return {
        "event_id": hashlib.sha256(event_material.encode("utf-8")).hexdigest()[:24],
        "symbol": symbol,
        "side": side,
        "signal_candle_ms": candle_ms,
        "analysis_ts": analysis_ts,
        "entry": entry,
        "stop_loss": stop,
        "take_profit_1": tp1,
        "take_profit_2": tp2,
        "score": score,
        "score_label": item.get("confirmed_score_label"),
        "model_name": item.get("model_name"),
        "model_status": model_status,
        "research_only": model_status != "PRODUCTION",
        "notification_only": True,
        "auto_trade_authorized": False,
    }


def build_alert_payload(market_status, symbol_cache, symbols, now):
    """Build a read-only alerts envelope; any unhealthy heartbeat fails closed."""
    if not isinstance(market_status, dict) or not _finite_number(now) or now <= 0:
        return {
            "generated_at": now if _finite_number(now) else None,
            "status": "DELAYED",
            "alerts": [],
            "notification_only": True,
            "auto_trade_authorized": False,
        }

    heartbeat_status = market_status.get("status")
    updated = market_status.get("updated")
    heartbeat_fresh = (
        heartbeat_status == "LIVE"
        and _finite_number(updated)
        and updated > 0
        and updated <= now
        and now - updated <= 120
    )
    alerts = []
    if heartbeat_fresh and isinstance(symbol_cache, dict):
        for symbol in symbols if isinstance(symbols, (list, tuple)) else ():
            alert = validate_alert_candidate(symbol, symbol_cache.get(symbol), now)
            if alert is not None:
                alerts.append(alert)

    return {
        "generated_at": now,
        "status": "LIVE" if heartbeat_fresh else "DELAYED",
        "alerts": alerts,
        "notification_only": True,
        "auto_trade_authorized": False,
    }
