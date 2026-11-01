"""Fail-closed, durable email notification outbox.

This module sends notifications only. It never places or manages exchange orders.
Sending is disabled unless ALERT_EMAIL_ENABLED=true and all SMTP settings are
provided through environment variables. No credentials belong in source control.
"""
from __future__ import annotations

import json
import os
import smtplib
import ssl
import tempfile
import time
from email.message import EmailMessage
from pathlib import Path
from threading import RLock
from typing import Callable


class EmailConfigurationError(ValueError):
    pass


class EmailOutboxError(RuntimeError):
    pass


def _truthy(value: str | None) -> bool:
    return isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "on"}


def _clean_addresses(value: str) -> list[str]:
    addresses = []
    for part in value.split(","):
        address = part.strip()
        if address and address not in addresses:
            addresses.append(address)
    return addresses


class EmailConfig:
    """SMTP settings loaded only from environment; disabled by default."""

    def __init__(self, *, enabled: bool, host: str = "", port: int = 587,
                 username: str = "", password: str = "", sender: str = "",
                 recipients: tuple[str, ...] = (), timeout: float = 10.0):
        self.enabled = enabled
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.sender = sender
        self.recipients = recipients
        self.timeout = timeout

    @classmethod
    def from_env(cls, environ=None):
        env = os.environ if environ is None else environ
        enabled = _truthy(env.get("ALERT_EMAIL_ENABLED"))
        recipients = tuple(_clean_addresses(env.get("ALERT_EMAIL_RECIPIENTS", "")))
        try:
            port = int(env.get("ALERT_SMTP_PORT", "587"))
            timeout = float(env.get("ALERT_SMTP_TIMEOUT", "10"))
        except (TypeError, ValueError) as exc:
            raise EmailConfigurationError("Invalid SMTP port or timeout") from exc
        config = cls(
            enabled=enabled,
            host=env.get("ALERT_SMTP_HOST", "").strip(),
            port=port,
            username=env.get("ALERT_SMTP_USERNAME", ""),
            password=env.get("ALERT_SMTP_PASSWORD", ""),
            sender=env.get("ALERT_EMAIL_SENDER", "").strip(),
            recipients=recipients,
            timeout=timeout,
        )
        if enabled:
            config.validate()
        return config

    def validate(self):
        if not self.host or not self.sender or not self.recipients:
            raise EmailConfigurationError("Enabled email requires host, sender, and recipients")
        if not (1 <= self.port <= 65535) or not (1 <= self.timeout <= 60):
            raise EmailConfigurationError("SMTP port or timeout is outside safe bounds")
        if not self.username or not self.password:
            raise EmailConfigurationError("Enabled email requires SMTP credentials")
        if any("\r" in x or "\n" in x for x in (
            self.host, self.username, self.password, self.sender, *self.recipients
        )):
            raise EmailConfigurationError("Invalid newline in email configuration")


class EmailOutbox:
    """Atomic JSON outbox with event idempotency and bounded retry scheduling.

    The default is inert: callers must explicitly pass enabled=True and provide
    a transport. Persisted state is a single-process local ledger, not a
    distributed queue. Do not share it across multiple application workers.
    """

    SCHEMA_VERSION = 1

    def __init__(self, storage_path, *, enabled=False, max_attempts=5,
                 base_retry_seconds=30, clock: Callable[[], float] = time.time):
        self.path = Path(storage_path)
        self.enabled = bool(enabled)
        self.max_attempts = int(max_attempts)
        self.base_retry_seconds = int(base_retry_seconds)
        self.clock = clock
        self._lock = RLock()
        if self.max_attempts < 1 or self.max_attempts > 20 or self.base_retry_seconds < 1:
            raise ValueError("Invalid retry policy")
        self._state = self._load()

    def _empty(self):
        return {"schema_version": self.SCHEMA_VERSION, "events": {}}

    def _load(self):
        if not self.path.exists():
            return self._empty()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise EmailOutboxError("Outbox ledger is unreadable; refusing to send") from exc
        if (not isinstance(data, dict) or type(data.get("schema_version")) is not int
                or data.get("schema_version") != self.SCHEMA_VERSION
                or not isinstance(data.get("events"), dict)):
            raise EmailOutboxError("Unsupported outbox ledger schema; refusing to send")
        self._validate_ledger_events(data["events"])
        return data

    @staticmethod
    def _finite_number(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

    def _validate_ledger_events(self, events):
        allowed_fields = {
            "event_id", "subject", "body", "recipients", "pending_recipients",
            "status", "attempts", "created_at", "next_attempt_at", "last_error",
        }
        allowed_statuses = {"PENDING", "RETRY", "SENT", "DEAD"}
        for key, event in events.items():
            if not isinstance(key, str) or not key or len(key) > 160 or not isinstance(event, dict):
                raise EmailOutboxError("Invalid outbox event; refusing to send")
            if set(event) != allowed_fields or event.get("event_id") != key:
                raise EmailOutboxError("Invalid outbox event schema; refusing to send")
            recipients = event.get("recipients")
            pending = event.get("pending_recipients")
            if (not isinstance(recipients, list) or not recipients
                    or not all(isinstance(x, str) and x and "\r" not in x and "\n" not in x for x in recipients)
                    or len(set(recipients)) != len(recipients)
                    or not isinstance(pending, list)
                    or not all(isinstance(x, str) for x in pending)
                    or not set(pending).issubset(set(recipients))):
                raise EmailOutboxError("Invalid outbox recipients; refusing to send")
            if (not isinstance(event.get("subject"), str) or not event["subject"].strip()
                    or "\r" in event["subject"] or "\n" in event["subject"]
                    or not isinstance(event.get("body"), str) or not event["body"].strip()):
                raise EmailOutboxError("Invalid outbox message; refusing to send")
            attempts = event.get("attempts")
            if (not isinstance(event.get("status"), str) or event.get("status") not in allowed_statuses
                    or type(attempts) is not int
                    or attempts < 0 or attempts > self.max_attempts
                    or not self._finite_number(event.get("created_at"))
                    or not self._finite_number(event.get("next_attempt_at"))):
                raise EmailOutboxError("Invalid outbox state; refusing to send")
            last_error = event.get("last_error")
            if last_error is not None and (not isinstance(last_error, str) or len(last_error) > 120):
                raise EmailOutboxError("Invalid outbox error metadata; refusing to send")
            if event["status"] == "SENT" and pending:
                raise EmailOutboxError("Sent event still has pending recipients; refusing to send")
            if event["status"] in {"PENDING", "RETRY"} and not pending:
                raise EmailOutboxError("Pending event has no recipients; refusing to send")
            if event["status"] == "RETRY" and attempts >= self.max_attempts:
                raise EmailOutboxError("Retry event exceeds retry limit; refusing to send")

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp",
                                         dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(self._state, stream, ensure_ascii=False, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, self.path)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise

    def enqueue(self, *, event_id, subject, body, recipients):
        if not isinstance(event_id, str) or not event_id.strip() or len(event_id) > 160:
            raise ValueError("event_id must be a non-empty bounded string")
        if not isinstance(subject, str) or not subject.strip() or "\r" in subject or "\n" in subject:
            raise ValueError("Invalid email subject")
        if not isinstance(body, str) or not body.strip():
            raise ValueError("Email body must be non-empty text")
        clean = _clean_addresses(",".join(recipients) if isinstance(recipients, (list, tuple)) else "")
        if not clean or any("\r" in x or "\n" in x for x in clean):
            raise ValueError("At least one valid recipient is required")
        with self._lock:
            if event_id in self._state["events"]:
                return False
            self._state["events"][event_id] = {
                "event_id": event_id, "subject": subject, "body": body,
                "recipients": clean, "pending_recipients": clean,
                "status": "PENDING", "attempts": 0, "created_at": self.clock(),
                "next_attempt_at": self.clock(), "last_error": None,
            }
            self._save()
            return True

    def snapshot(self):
        with self._lock:
            return json.loads(json.dumps(self._state))

    def send_due(self, config: EmailConfig, *, transport=None, limit=20):
        """Send due messages. A transport can be injected for deterministic tests."""
        if not self.enabled or not config.enabled:
            return {"sent": 0, "failed": 0, "disabled": True}
        config.validate()
        if not isinstance(limit, int) or limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        sender = transport or self._smtp_transport
        sent = failed = 0
        now = self.clock()
        with self._lock:
            due = [e for e in self._state["events"].values()
                   if e["status"] in {"PENDING", "RETRY"}
                   and e["next_attempt_at"] <= now][:limit]
        for event in due:
            # Never let a queued event expand the configured recipient allowlist.
            if not set(event["pending_recipients"]).issubset(set(config.recipients)):
                with self._lock:
                    current = self._state["events"][event["event_id"]]
                    current["status"] = "DEAD"
                    current["last_error"] = "Recipient not present in configured allowlist"
                    self._save()
                failed += 1
                continue
            message = EmailMessage()
            message["From"] = config.sender
            message["To"] = ", ".join(event["pending_recipients"])
            message["Subject"] = event["subject"]
            message.set_content(event["body"])
            try:
                refused = sender(config, message, event["pending_recipients"])
                refused = refused or {}
                with self._lock:
                    current = self._state["events"][event["event_id"]]
                    failed_recipients = [r for r in current["pending_recipients"] if r in refused]
                    if failed_recipients:
                        current["pending_recipients"] = failed_recipients
                        self._record_failure(current, "One or more recipients were refused")
                        failed += 1
                    else:
                        current["pending_recipients"] = []
                        current["status"] = "SENT"
                        current["sent_at"] = self.clock()
                        current["last_error"] = None
                        sent += 1
                    self._save()
            except Exception as exc:
                with self._lock:
                    current = self._state["events"][event["event_id"]]
                    self._record_failure(current, type(exc).__name__)
                    self._save()
                failed += 1
        return {"sent": sent, "failed": failed, "disabled": False}

    def _record_failure(self, event, reason):
        event["attempts"] += 1
        event["last_error"] = str(reason)[:120]
        if event["attempts"] >= self.max_attempts:
            event["status"] = "DEAD"
        else:
            event["status"] = "RETRY"
            event["next_attempt_at"] = self.clock() + min(
                self.base_retry_seconds * (2 ** (event["attempts"] - 1)), 3600
            )

    @staticmethod
    def _smtp_transport(config, message, recipients):
        # STARTTLS is mandatory; plaintext SMTP is never used.
        with smtplib.SMTP(config.host, config.port, timeout=config.timeout) as client:
            client.ehlo()
            client.starttls(context=ssl.create_default_context())
            client.ehlo()
            client.login(config.username, config.password)
            return client.send_message(message, from_addr=config.sender,
                                       to_addrs=list(recipients))
