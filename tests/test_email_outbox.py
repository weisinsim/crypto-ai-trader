import json
import tempfile
import unittest
from pathlib import Path

from notifications.email_outbox import (
    EmailConfig, EmailConfigurationError, EmailOutbox, EmailOutboxError,
)


class EmailOutboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "outbox.json"
        self.now = [1000.0]
        self.clock = lambda: self.now[0]
        self.config = EmailConfig(
            enabled=True, host="smtp.example.test", port=587,
            username="test-user", password="not-a-real-secret",
            sender="alerts@example.test",
            recipients=("one@example.test", "two@example.test"),
        )
        self.outbox = EmailOutbox(self.path, enabled=True, clock=self.clock)

    def tearDown(self):
        self.temp.cleanup()

    def enqueue(self):
        return self.outbox.enqueue(
            event_id="event-001", subject="Signal notification",
            body="Notification only; no order was placed.",
            recipients=["one@example.test", "two@example.test"],
        )

    def test_disabled_by_default_and_does_not_call_transport(self):
        outbox = EmailOutbox(self.path, enabled=False, clock=self.clock)
        self.assertTrue(outbox.enqueue(
            event_id="e", subject="s", body="b", recipients=["a@example.test"]
        ))
        called = []
        result = outbox.send_due(self.config, transport=lambda *args: called.append(args))
        self.assertTrue(result["disabled"])
        self.assertEqual(called, [])

    def test_environment_configuration_is_disabled_by_default(self):
        config = EmailConfig.from_env({})
        self.assertFalse(config.enabled)

    def test_enabled_configuration_requires_complete_credentials(self):
        with self.assertRaises(EmailConfigurationError):
            EmailConfig.from_env({"ALERT_EMAIL_ENABLED": "true"})

    def test_smtp_credentials_reject_newlines(self):
        with self.assertRaises(EmailConfigurationError):
            EmailConfig(
                enabled=True, host="smtp.example.test", port=587,
                username="user\nInjected: yes", password="secret",
                sender="alerts@example.test", recipients=("one@example.test",),
            ).validate()

    def test_enqueue_is_durable_and_idempotent_across_restart(self):
        self.assertTrue(self.enqueue())
        self.assertFalse(self.enqueue())
        restarted = EmailOutbox(self.path, enabled=True, clock=self.clock)
        events = restarted.snapshot()["events"]
        self.assertEqual(list(events), ["event-001"])
        self.assertEqual(events["event-001"]["status"], "PENDING")

    def test_success_marks_event_sent(self):
        self.enqueue()
        calls = []
        result = self.outbox.send_due(
            self.config, transport=lambda config, message, recipients: calls.append(
                (message["Subject"], list(recipients))
            ) or {},
        )
        self.assertEqual(result, {"sent": 1, "failed": 0, "disabled": False})
        self.assertEqual(calls[0][1], ["one@example.test", "two@example.test"])
        self.assertEqual(self.outbox.snapshot()["events"]["event-001"]["status"], "SENT")
        restarted = EmailOutbox(self.path, enabled=True, clock=self.clock)
        self.assertEqual(restarted.snapshot()["events"]["event-001"]["status"], "SENT")

    def test_failure_is_retried_with_backoff(self):
        self.enqueue()
        for attempt in range(2):
            result = self.outbox.send_due(
                self.config, transport=lambda *args: (_ for _ in ()).throw(TimeoutError())
            )
            self.assertEqual(result["failed"], 1)
            event = self.outbox.snapshot()["events"]["event-001"]
            self.assertEqual(event["status"], "RETRY")
            self.assertEqual(event["attempts"], attempt + 1)
            self.now[0] = event["next_attempt_at"]
        result = self.outbox.send_due(
            self.config, transport=lambda *args: (_ for _ in ()).throw(TimeoutError())
        )
        self.assertEqual(result["failed"], 1)
        event = self.outbox.snapshot()["events"]["event-001"]
        self.assertEqual(event["status"], "RETRY")
        self.assertEqual(event["attempts"], 3)
        self.assertEqual(event["last_error"], "TimeoutError")

    def test_partial_recipient_refusal_retries_only_refused_recipient(self):
        self.enqueue()
        result = self.outbox.send_due(
            self.config,
            transport=lambda config, message, recipients: {"two@example.test": (550, b"rejected")},
        )
        self.assertEqual(result["failed"], 1)
        event = self.outbox.snapshot()["events"]["event-001"]
        self.assertEqual(event["pending_recipients"], ["two@example.test"])
        self.assertEqual(event["status"], "RETRY")
        self.now[0] = event["next_attempt_at"]
        result = self.outbox.send_due(self.config, transport=lambda *args: {})
        self.assertEqual(result["sent"], 1)
        self.assertEqual(
            self.outbox.snapshot()["events"]["event-001"]["pending_recipients"], []
        )

    def test_recipient_allowlist_blocks_unapproved_destination(self):
        self.outbox.enqueue(
            event_id="outside", subject="Signal", body="Notification only",
            recipients=["attacker@example.test"],
        )
        called = []
        result = self.outbox.send_due(
            self.config, transport=lambda *args: called.append(args) or {}
        )
        self.assertEqual(result["failed"], 1)
        self.assertEqual(called, [])
        event = self.outbox.snapshot()["events"]["outside"]
        self.assertEqual(event["status"], "DEAD")
        self.assertIn("allowlist", event["last_error"])

    def test_retry_limit_moves_event_to_dead_letter(self):
        outbox = EmailOutbox(
            self.path, enabled=True, max_attempts=2,
            base_retry_seconds=1, clock=self.clock,
        )
        outbox.enqueue(
            event_id="dead", subject="Signal", body="Notification only",
            recipients=["one@example.test"],
        )
        failing = lambda *args: (_ for _ in ()).throw(TimeoutError())
        outbox.send_due(self.config, transport=failing)
        event = outbox.snapshot()["events"]["dead"]
        self.now[0] = event["next_attempt_at"]
        outbox.send_due(self.config, transport=failing)
        self.assertEqual(outbox.snapshot()["events"]["dead"]["status"], "DEAD")

    def test_corrupt_or_unknown_ledger_fails_closed(self):
        self.path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(EmailOutboxError):
            EmailOutbox(self.path, enabled=True)
        self.path.write_text(json.dumps({"schema_version": 99, "events": {}}), encoding="utf-8")
        with self.assertRaises(EmailOutboxError):
            EmailOutbox(self.path, enabled=True)

    def test_malformed_persisted_event_fails_closed(self):
        self.enqueue()
        original = self.outbox.snapshot()["events"]["event-001"]
        invalid_events = []
        sent_with_pending = dict(original, status="SENT")
        invalid_events.append(sent_with_pending)
        mismatched_id = dict(original, event_id="different-id")
        invalid_events.append(mismatched_id)
        non_finite_time = dict(original, created_at=float("nan"))
        invalid_events.append(non_finite_time)
        oversized_integer_time = dict(original, created_at=10 ** 10000)
        invalid_events.append(oversized_integer_time)
        unknown_field = dict(original, unexpected="value")
        invalid_events.append(unknown_field)
        duplicate_pending = dict(original, pending_recipients=["one@example.test", "one@example.test"])
        invalid_events.append(duplicate_pending)

        for event in invalid_events:
            with self.subTest(event=event):
                self.path.write_text(json.dumps({
                    "schema_version": 1, "events": {"event-001": event}
                }), encoding="utf-8")
                with self.assertRaises(EmailOutboxError):
                    EmailOutbox(self.path, enabled=True, clock=self.clock)

    def test_enqueue_rejects_non_string_recipient_items(self):
        for recipients in (["valid@example.test", 123], [None], "valid@example.test"):
            with self.subTest(recipients=recipients):
                with self.assertRaises(ValueError):
                    self.outbox.enqueue(
                        event_id="bad-recipient", subject="Signal", body="Notification",
                        recipients=recipients,
                    )

    def test_invalid_subject_and_empty_body_are_rejected(self):
        with self.assertRaises(ValueError):
            self.outbox.enqueue(event_id="e", subject="bad\nsubject", body="b",
                                recipients=["a@example.test"])
        with self.assertRaises(ValueError):
            self.outbox.enqueue(event_id="e", subject="subject", body=" ",
                                recipients=["a@example.test"])


if __name__ == "__main__":
    unittest.main()
