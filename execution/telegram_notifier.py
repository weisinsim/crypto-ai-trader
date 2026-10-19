"""Optional Telegram delivery adapter for entry-alert messages.

Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in the runtime environment.
Never prints credentials. This sends notifications only; it cannot place orders.
"""
from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from execution.alert_message import format_entry_alert


class NotificationDeliveryError(RuntimeError):
    pass


def send_telegram_alert(event, *, token=None, chat_id=None, timeout=10, opener=urlopen):
    """Send one eligible alert through Telegram Bot API.

    Returns a minimal delivery receipt. Callers should mark the outbox event
    delivered only after this function returns successfully.
    """
    token = token if token is not None else os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id if chat_id is not None else os.environ.get("TELEGRAM_CHAT_ID")
    if not isinstance(token, str) or not token.strip():
        raise NotificationDeliveryError("missing_telegram_bot_token")
    if not isinstance(chat_id, str) or not chat_id.strip():
        raise NotificationDeliveryError("missing_telegram_chat_id")
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        raise ValueError("timeout must be positive")

    message = format_entry_alert(event)
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = urlencode({"chat_id": chat_id, "text": message, "disable_web_page_preview": "true"}).encode()
    request = Request(url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    try:
        with opener(request, timeout=timeout) as response:
            raw = response.read()
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise NotificationDeliveryError("telegram_transport_failed") from exc

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NotificationDeliveryError("telegram_invalid_response") from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise NotificationDeliveryError("telegram_rejected_message")
    result = payload.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("message_id"), int):
        raise NotificationDeliveryError("telegram_missing_message_id")
    return {"provider": "telegram", "message_id": result["message_id"], "delivered": True}
