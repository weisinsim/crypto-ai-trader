"""Optional Telegram delivery for human-facing entry alerts.

This module never places trades. Configure credentials only in the deployment
secret store. Delivery is at-least-once: a process crash after Telegram accepts
a message but before SQLite is marked delivered can cause a duplicate.
"""
from __future__ import annotations

import json
import math
import os
from urllib.request import Request, urlopen


def format_alert(event):
    """Format a compact, escaped-by-JSON Telegram text from a validated alert."""
    if not isinstance(event, dict):
        raise ValueError("event must be an object")
    if event.get("eligible") is not True:
        raise ValueError("event is not eligible")
    if event.get("side") not in ("LONG", "SHORT"):
        raise ValueError("side must be LONG or SHORT")
    if event.get("notification_only") is not True or event.get("auto_trade_authorized") is not False:
        raise ValueError("only notification-only events are allowed")

    symbol = event.get("symbol")
    if not isinstance(symbol, str) or not symbol or len(symbol) > 32:
        raise ValueError("invalid symbol")

    lines = ["ENTRY SIGNAL — NOT AN ORDER", f"{symbol} | {event['side']}"]
    for key, label in (
        ("entry_price", "Entry"),
        ("stop_loss", "Stop loss"),
        ("take_profit", "Take profit"),
        ("risk_reward", "Risk/reward"),
    ):
        value = event.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{key} must be finite numeric data")
        lines.append(f"{label}: {value:g}")
    event_id = event.get("event_id")
    if isinstance(event_id, str) and event_id:
        lines.append(f"Event: {event_id[:80]}")
    lines.append("Research/alert only. Verify independently before acting.")
    return "\n".join(lines)


def send_telegram_message(text, *, token, chat_id, timeout=8, opener=urlopen):
    """Send one Telegram message. Secrets must come from secure environment config."""
    if not isinstance(token, str) or not token.strip() or not isinstance(chat_id, str) or not chat_id.strip():
        raise ValueError("Telegram token and chat ID are required")
    if not isinstance(text, str) or not text or len(text) > 4096:
        raise ValueError("message must contain 1–4096 characters")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be finite and positive")
    url = f"https://api.telegram.org/bot{token.strip()}/sendMessage"
    body = json.dumps({"chat_id": chat_id.strip(), "text": text}, separators=(",", ":")).encode("utf-8")
    request = Request(url, data=body, headers={"Content-Type": "application/json", "Accept": "application/json"}, method="POST")
    with opener(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RuntimeError("telegram_delivery_rejected")
    return payload.get("result", {}).get("message_id")


def deliver_pending(store, *, token=None, chat_id=None, sender=send_telegram_message, limit=20, emit=print):
    """Deliver pending alerts, marking only confirmed Telegram responses as delivered."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "") if token is None else token
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "") if chat_id is None else chat_id
    if not token or not chat_id:
        raise RuntimeError("telegram_credentials_not_configured")
    sent = 0
    failed = 0
    for item in store.pending(limit=limit):
        event_id = item.get("event_id")
        try:
            message = format_alert(item.get("event"))
            message_id = sender(message, token=token, chat_id=chat_id)
            if not store.mark_delivered(event_id):
                # Do not claim success if durable outbox state could not be advanced.
                failed += 1
                emit(json.dumps({"status": "DEGRADED", "reason": "mark_delivered_failed", "event_id": event_id}, separators=(",", ":")))
                continue
            sent += 1
            emit(json.dumps({"status": "DELIVERED", "event_id": event_id, "message_id": message_id}, separators=(",", ":")))
        except Exception as exc:
            failed += 1
            # Avoid logging request URLs or credentials; expose only exception class.
            emit(json.dumps({"status": "PENDING", "reason": type(exc).__name__, "event_id": event_id}, separators=(",", ":")))
    return {"sent": sent, "failed": failed}
