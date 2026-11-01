import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as app_module
from notifications.email_outbox import EmailConfig


class EmailNotificationIntegrationTests(unittest.TestCase):
    def tearDown(self):
        app_module.email_config = None
        app_module.email_outbox = None

    def test_email_is_disabled_without_explicit_opt_in(self):
        self.assertFalse(app_module.configure_email_notifications({}))
        self.assertIsNone(app_module.email_config)
        self.assertIsNone(app_module.email_outbox)

    def test_email_requires_single_process_acknowledgement(self):
        self.assertFalse(app_module.configure_email_notifications({
            "ALERT_EMAIL_ENABLED": "true",
        }))
        self.assertIsNone(app_module.email_outbox)

    def test_email_refuses_known_multi_worker_configuration(self):
        self.assertFalse(app_module.configure_email_notifications({
            "ALERT_EMAIL_ENABLED": "true",
            "ALERT_EMAIL_SINGLE_PROCESS_ACK": "true",
            "WEB_CONCURRENCY": "2",
        }))
        self.assertIsNone(app_module.email_outbox)

    def test_valid_opt_in_initializes_outbox_without_sending(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {
                "ALERT_EMAIL_ENABLED": "true",
                "ALERT_EMAIL_SINGLE_PROCESS_ACK": "true",
                "ALERT_SMTP_HOST": "smtp.example.test",
                "ALERT_SMTP_PORT": "587",
                "ALERT_SMTP_USERNAME": "test-user",
                "ALERT_SMTP_PASSWORD": "test-password",
                "ALERT_EMAIL_SENDER": "alerts@example.test",
                "ALERT_EMAIL_RECIPIENTS": "one@example.test,two@example.test",
                "ALERT_EMAIL_OUTBOX_PATH": str(Path(directory) / "outbox.json"),
            }
            self.assertTrue(app_module.configure_email_notifications(env))
            self.assertTrue(app_module.email_config.enabled)
            self.assertTrue(app_module.email_outbox.enabled)
            self.assertFalse(Path(env["ALERT_EMAIL_OUTBOX_PATH"]).exists())

    def test_signal_queue_uses_validated_alert_and_allowlisted_recipients(self):
        item = {
            "confirmed_signal": "LONG",
            "model_status": "CANDIDATE",
            "model_validation_passed": True,
            "ts": 1_800_000_000.0 - 5,
            "price": 100.0,
            "confirmed_signal_time": (1_800_000_000.0 - 60) * 1000,
            "analysis_ts": 1_800_000_000.0 - 30,
            "confirmed_entry": 100.0,
            "confirmed_sl": 98.0,
            "confirmed_tp1": 103.0,
            "confirmed_tp2": 106.0,
            "confirmed_score": 75.0,
            "confirmed_score_label": "A",
            "model_name": "BTC Candidate Model",
        }
        queued = []
        class FakeOutbox:
            def enqueue(self, **kwargs):
                queued.append(kwargs)
                return True

        app_module.email_config = EmailConfig(
            enabled=True, host="smtp.example.test", username="user",
            password="password", sender="alerts@example.test",
            recipients=("one@example.test", "two@example.test"),
        )
        app_module.email_outbox = FakeOutbox()
        with patch.dict(app_module.cache, {"BTCUSDT": item}), patch.object(
            app_module.time, "time", return_value=1_800_000_000.0
        ):
            self.assertTrue(app_module.queue_signal_email("BTCUSDT"))

        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]["recipients"], ["one@example.test", "two@example.test"])
        self.assertIn("Notification only", queued[0]["body"])
        self.assertIn("No exchange order was placed", queued[0]["body"])


if __name__ == "__main__":
    unittest.main()
