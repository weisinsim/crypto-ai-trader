"""Environment-configured email alerts for signal-only trading notifications.

No exchange credentials or order execution are used here. SMTP secrets must be
provided by the deployment environment and must never be committed to source control.
"""
from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from email.utils import formatdate
from typing import Mapping


DEFAULT_ALERT_RECIPIENTS = (\n    "wei_sin_sim@hotmail.com",\n    "weisinsim2018@gmail.com",\n)\n\n\ndef notification_config(environ: Mapping[str, str] | None = None) -> dict:\n    env = os.environ if environ is None else environ\n    configured = all((env.get("SMTP_HOST", "").strip(), env.get("SMTP_PASSWORD", ""),\n                      (env.get("ALERT_EMAIL_FROM", "").strip() or env.get("SMTP_USER", "").strip())))\n    raw = env.get("ALERT_EMAIL_TO", "").strip()\n    recipients = [item.strip() for item in raw.split(",") if item.strip()] if raw else list(DEFAULT_ALERT_RECIPIENTS)\n    return {"configured": bool(configured), "status": "CONFIGURED" if configured else "NOT_CONFIGURED",\n            "recipients": recipients, "events": list(EVENT_LABELS)}\n\n\nEVENT_LABELS = {
    "ENTRY_CONFIRMED": "Confirmed entry signal",
    "TAKE_PROFIT_1": "Take-profit level 1 reached",
    "TAKE_PROFIT_2": "Take-profit level 2 reached",
    "STOP_LOSS": "Stop-loss level reached",
    "SIGNAL_INVALIDATED": "Trading signal invalidated",
}


def build_alert_message(event: Mapping[str, object], *, sender: str, recipient: str | list[str]) -> EmailMessage:
    event_type = str(event.get("event", "SIGNAL_UPDATE"))
    symbol = str(event.get("symbol", "UNKNOWN"))
    direction = str(event.get("direction", "UNKNOWN"))
    label = EVENT_LABELS.get(event_type, "Trading signal update")
    subject = f"[Crypto AI Trader] {label}: {symbol} {direction}"
    lines = [
        label,
        f"Symbol: {symbol}",
        f"Direction: {direction}",
    ]
    fields = (
        ("Current price", "price"),
        ("Entry", "entry"),
        ("Stop loss", "sl"),
        ("Triggered level", "level"),
        ("Take profit 1", "tp1"),
        ("Take profit 2", "tp2"),
        ("Model score", "score"),
        ("Signal time (ms)", "signal_time"),
    )
    for label_text, key in fields:
        value = event.get(key)
        if value is not None:
            lines.append(f"{label_text}: {value}")
    lines.extend([
        "",
        "This is an informational signal only. No order has been placed.",
        "Confirm current market conditions and your actual position before acting.",
    ])
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = recipient
    message["Date"] = formatdate(localtime=False)
    message.set_content("\n".join(lines))
    return message


def send_email_alert(
    event: Mapping[str, object],
    *,
    environ: Mapping[str, str] | None = None,
    smtp_factory=None,
) -> bool:
    """Send one alert. Returns False when SMTP is not fully configured."""
    env = os.environ if environ is None else environ
    host = env.get("SMTP_HOST", "").strip()
    recipient = env.get("ALERT_EMAIL_TO", "").strip()
    sender = env.get("ALERT_EMAIL_FROM", "").strip() or env.get("SMTP_USER", "").strip()
    password = env.get("SMTP_PASSWORD", "")
    if not all((host, sender, password)) or not recipient:
        return False

    try:
        port = int(env.get("SMTP_PORT", "587"))
        timeout = float(env.get("SMTP_TIMEOUT_SECONDS", "10"))
    except (TypeError, ValueError):
        raise ValueError("SMTP_PORT and SMTP_TIMEOUT_SECONDS must be numeric")
    if not (1 <= port <= 65535) or not (0 < timeout <= 60):
        raise ValueError("SMTP port or timeout is outside the permitted range")

    username = env.get("SMTP_USER", "").strip() or sender
    use_ssl = env.get("SMTP_USE_SSL", "false").strip().lower() == "true"
    if smtp_factory is None:
        smtp_factory = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
    message = build_alert_message(event, sender=sender, recipient=recipient)

    with smtp_factory(host, port, timeout=timeout) as client:
        if not use_ssl:
            client.starttls()
        client.login(username, password)
        client.send_message(message)
    return True
