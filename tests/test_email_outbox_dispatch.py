import asyncio
import tempfile
import unittest
from pathlib import Path

from validation.email_outbox import EmailOutbox, dispatch_outbox_batch


class EmailOutboxDispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.outbox = EmailOutbox(Path(self.tmp.name) / "dispatch.sqlite3",
                                  max_attempts=2, retry_base_seconds=1)

    def tearDown(self):
        self.tmp.cleanup()

    def event(self, event_id):
        return {"event_id": event_id, "event": "ENTRY_CONFIRMED",
                "symbol": "ETHUSDT", "direction": "LONG"}

    def test_dispatch_marks_smtp_accepted_event_sent(self):
        event = self.event("ETHUSDT:100:ENTRY")
        self.outbox.enqueue(event, now=100)
        sent = []

        async def fake_send(payload):
            sent.append(payload)
            return True

        result = asyncio.run(dispatch_outbox_batch(self.outbox, fake_send, configured=True))
        self.assertEqual(result, "SENT_TO_SMTP")
        self.assertEqual(sent, [event])
        row = self.outbox.list_recent()[0]
        self.assertEqual(row["status"], "SENT")
        self.assertEqual(row["attempts"], 1)

    def test_dispatch_records_failure_for_retry(self):
        self.outbox.enqueue(self.event("SOLUSDT:200:TP1"), now=100)

        async def fake_send(_payload):
            raise RuntimeError("fake SMTP failure")

        result = asyncio.run(dispatch_outbox_batch(self.outbox, fake_send, configured=True))
        self.assertEqual(result, "FAILED")
        row = self.outbox.list_recent()[0]
        self.assertEqual(row["status"], "PENDING")
        self.assertEqual(row["attempts"], 1)
        self.assertIn("fake SMTP failure", row["last_error"])

    def test_unconfigured_smtp_leaves_event_pending_without_consuming_attempt(self):
        self.outbox.enqueue(self.event("XRPUSDT:300:STOP"), now=100)

        async def should_not_send(_payload):
            self.fail("sender must not run when SMTP is not configured")

        result = asyncio.run(dispatch_outbox_batch(self.outbox, should_not_send, configured=False))
        self.assertEqual(result, "NOT_CONFIGURED")
        row = self.outbox.list_recent()[0]
        self.assertEqual(row["status"], "PENDING")
        self.assertEqual(row["attempts"], 0)

    def test_empty_queue_is_idle(self):
        async def should_not_send(_payload):
            self.fail("no event should be sent")

        result = asyncio.run(dispatch_outbox_batch(self.outbox, should_not_send, configured=True))
        self.assertEqual(result, "IDLE")


if __name__ == "__main__":
    unittest.main()
