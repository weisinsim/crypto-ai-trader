import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as app_module
from notifications.email_outbox import EmailConfig, EmailOutboxError


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
            def __init__(self):
                self.event_ids = set()

            def enqueue(self, **kwargs):
                event_id = kwargs["event_id"]
                if event_id in self.event_ids:
                    return False
                self.event_ids.add(event_id)
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


    def _install_confirmable_candidate(self, now):
        item = {
            "signal": "LONG",
            "model_status": "CANDIDATE",
            "ts": now - 5,
            "price": 100.0,
            "analysis_ts": now - 30,
            "entry": 100.0,
            "sl": 98.0,
            "tp1": 103.0,
            "tp2": 106.0,
            "score": 82.0,
            "score_label": "A",
            "opportunity_tier": "A",
            "model_name": "BTC Candidate Model",
        }
        config = {
            "name": "BTC Candidate Model",
            "status": "CANDIDATE",
            "validation": {
                "holdout_passed": True,
                "walk_forward_passed": True,
                "cost_stress_passed": True,
                "report_id": "independent-report-001",
            },
        }
        app_module.email_config = EmailConfig(
            enabled=True, host="smtp.example.test", username="user",
            password="password", sender="alerts@example.test",
            recipients=("one@example.test",),
        )
        queued = []

        class FakeOutbox:
            def enqueue(self, **kwargs):
                queued.append(kwargs)
                return True

        app_module.email_outbox = FakeOutbox()
        return item, config, queued

    def test_confirmed_validated_signal_queues_exactly_one_email(self):
        now = 1_800_000_000.0
        item, config, queued = self._install_confirmable_candidate(now)
        with patch.dict(app_module.cache, {"BTCUSDT": item}), patch.dict(
            app_module.MODEL_LIBRARY, {"BTCUSDT": config}
        ), patch.dict(app_module.confirmed_state, {"BTCUSDT": "NO-TRADE"}), patch.dict(
            app_module.signal_history, {"BTCUSDT": []}
        ), patch.object(app_module.time, "time", return_value=now):
            self.assertTrue(app_module.record_confirmed_signal(
                "BTCUSDT", candle_close_ms=int((now - 60) * 1000)
            ))
            self.assertEqual(len(queued), 1)
            self.assertEqual(queued[0]["recipients"], ["one@example.test"])
            self.assertIn("Notification only", queued[0]["body"])
            self.assertIn("No exchange order was placed", queued[0]["body"])

            # A repeated evaluation of the same confirmed direction must not enqueue again.
            self.assertFalse(app_module.record_confirmed_signal(
                "BTCUSDT", candle_close_ms=int((now - 60) * 1000)
            ))
            self.assertEqual(len(queued), 1)

            # Revoking any independent-validation gate invalidates the confirmation
            # and must not create another notification.
            config["validation"]["cost_stress_passed"] = False
            self.assertFalse(app_module.record_confirmed_signal(
                "BTCUSDT", candle_close_ms=int((now + 3600) * 1000)
            ))
            self.assertEqual(len(queued), 1)
            self.assertEqual(item["confirmed_signal"], "NO-TRADE")
            self.assertEqual(item["confirmed_status"], "WAITING_VALIDATION")

    def test_unvalidated_candidate_never_queues_email(self):
        now = 1_800_000_000.0
        item, config, queued = self._install_confirmable_candidate(now)
        config["validation"]["holdout_passed"] = False
        with patch.dict(app_module.cache, {"BTCUSDT": item}), patch.dict(
            app_module.MODEL_LIBRARY, {"BTCUSDT": config}
        ), patch.dict(app_module.confirmed_state, {"BTCUSDT": "NO-TRADE"}), patch.dict(
            app_module.signal_history, {"BTCUSDT": []}
        ), patch.object(app_module.time, "time", return_value=now):
            self.assertFalse(app_module.record_confirmed_signal(
                "BTCUSDT", candle_close_ms=int((now - 60) * 1000)
            ))
        self.assertEqual(queued, [])



    def test_same_confirmed_signal_retries_after_transient_outbox_failure(self):
        now = 1_800_000_000.0
        item, config, queued = self._install_confirmable_candidate(now)

        class FlakyOutbox:
            def __init__(self):
                self.calls = 0

            def enqueue(self, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise EmailOutboxError("simulated local persistence failure")
                queued.append(kwargs)
                return True

        flaky = FlakyOutbox()
        app_module.email_outbox = flaky
        with patch.dict(app_module.cache, {"BTCUSDT": item}), patch.dict(
            app_module.MODEL_LIBRARY, {"BTCUSDT": config}
        ), patch.dict(app_module.confirmed_state, {"BTCUSDT": "NO-TRADE"}), patch.dict(
            app_module.signal_history, {"BTCUSDT": []}
        ), patch.object(app_module.time, "time", return_value=now):
            self.assertTrue(app_module.record_confirmed_signal(
                "BTCUSDT", candle_close_ms=int((now - 60) * 1000)
            ))
            self.assertEqual(flaky.calls, 1)
            self.assertEqual(queued, [])

            # The direction is unchanged, but the event is still fresh and valid.
            # Retry the durable enqueue; the outbox event ID protects against duplicates.
            self.assertFalse(app_module.record_confirmed_signal(
                "BTCUSDT", candle_close_ms=int((now - 60) * 1000)
            ))
            self.assertEqual(flaky.calls, 2)
            self.assertEqual(len(queued), 1)


if __name__ == "__main__":
    unittest.main()
