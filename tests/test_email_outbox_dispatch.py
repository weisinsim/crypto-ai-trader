import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as trader_app
from validation.email_outbox import EmailOutbox


class EmailOutboxDispatchIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous_outbox = trader_app.email_outbox
        self.previous_status = trader_app.diagnostics.get("email_alert_status")
        trader_app.email_outbox = EmailOutbox(Path(self.tmp.name) / "dispatch.sqlite3",
                                               max_attempts=2, retry_base_seconds=1)

    def tearDown(self):
        trader_app.email_outbox = self.previous_outbox
        if self.previous_status is None:
            trader_app.diagnostics.pop("email_alert_status", None)
        else:
            trader_app.diagnostics["email_alert_status"] = self.previous_status
        self.tmp.cleanup()

    def test_dispatch_marks_smtp_accepted_event_sent(self):
        event = {"event_id": "ETHUSDT:100:ENTRY", "event": "ENTRY_CONFIRMED",
                 "symbol": "ETHUSDT", "direction": "LONG"}
        trader_app.queue_email_alert(event)
        with patch.object(trader_app, "notification_config", return_value={"configured": True}), \
             patch.object(trader_app, "send_email_alert", return_value=True) as send:
            asyncio.run(trader_app.dispatch_email_outbox_once())
        send.assert_called_once_with(event)
        row = trader_app.email_outbox.list_recent()[0]
        self.assertEqual(row["status"], "SENT")
        self.assertEqual(row["attempts"], 1)

    def test_dispatch_records_failure_for_retry(self):
        event = {"event_id": "SOLUSDT:200:TP1", "event": "TAKE_PROFIT_1",
                 "symbol": "SOLUSDT", "direction": "SHORT"}
        trader_app.queue_email_alert(event)
        with patch.object(trader_app, "notification_config", return_value={"configured": True}), \
             patch.object(trader_app, "send_email_alert", side_effect=RuntimeError("fake SMTP failure")):
            asyncio.run(trader_app.dispatch_email_outbox_once())
        row = trader_app.email_outbox.list_recent()[0]
        self.assertEqual(row["status"], "PENDING")
        self.assertEqual(row["attempts"], 1)
        self.assertIn("fake SMTP failure", row["last_error"])

    def test_unconfigured_smtp_leaves_event_pending_without_consuming_attempt(self):
        event = {"event_id": "XRPUSDT:300:STOP", "event": "STOP_LOSS",
                 "symbol": "XRPUSDT", "direction": "LONG"}
        trader_app.queue_email_alert(event)
        with patch.object(trader_app, "notification_config", return_value={"configured": False}), \
             patch.object(trader_app, "send_email_alert") as send:
            asyncio.run(trader_app.dispatch_email_outbox_once())
        send.assert_not_called()
        row = trader_app.email_outbox.list_recent()[0]
        self.assertEqual(row["status"], "PENDING")
        self.assertEqual(row["attempts"], 0)


if __name__ == "__main__":
    unittest.main()
