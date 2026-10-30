import unittest
from email.message import EmailMessage

from validation.email_notifications import build_alert_message, notification_config, send_email_alert


class FakeSMTP:
    def __init__(self, host, port, timeout):
        self.args = (host, port, timeout)
        self.started_tls = False
        self.logged_in = None
        self.message = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def starttls(self):
        self.started_tls = True

    def login(self, username, password):
        self.logged_in = (username, password)

    def send_message(self, message):
        self.message = message


class EmailNotificationTests(unittest.TestCase):
    def test_build_entry_email_contains_risk_levels_and_manual_execution_notice(self):
        message = build_alert_message({
            "event": "ENTRY_CONFIRMED", "symbol": "BTCUSDT", "direction": "LONG",
            "entry": 65000, "sl": 64500, "tp1": 66000, "tp2": 67000,
        }, sender="bot@example.com", recipient="user@example.com")
        self.assertEqual(message["To"], "user@example.com")
        self.assertIn("BTCUSDT LONG", message["Subject"])
        body = message.get_content()
        self.assertIn("Entry: 65000", body)
        self.assertIn("Stop loss: 64500", body)
        self.assertIn("Take profit 2: 67000", body)
        self.assertIn("No order has been placed", body)

    def test_default_recipients_are_both_configured_addresses(self):\n        config = notification_config({})\n        self.assertEqual(config["recipients"], ["wei_sin_sim@hotmail.com", "weisinsim2018@gmail.com"])\n        self.assertEqual(config["status"], "NOT_CONFIGURED")\n\n    def test_multiple_recipients_are_sent_in_one_message(self):\n        instances = []\n        def factory(host, port, timeout):\n            client = FakeSMTP(host, port, timeout)\n            instances.append(client)\n            return client\n        env = {"SMTP_HOST":"smtp.example.com", "SMTP_USER":"bot@example.com", "SMTP_PASSWORD":"secret"}\n        self.assertTrue(send_email_alert({"event":"ENTRY_CONFIRMED"}, environ=env, smtp_factory=factory))\n        self.assertEqual(instances[0].message["To"], "wei_sin_sim@hotmail.com, weisinsim2018@gmail.com")\n\n    def test_missing_smtp_configuration_does_not_attempt_network(self):
        self.assertFalse(send_email_alert({"event": "STOP_LOSS"}, environ={}))

    def test_send_uses_starttls_and_environment_credentials(self):
        instances = []
        def factory(host, port, timeout):
            client = FakeSMTP(host, port, timeout)
            instances.append(client)
            return client
        env = {
            "SMTP_HOST": "smtp.example.com", "SMTP_PORT": "587",
            "SMTP_USER": "bot@example.com", "SMTP_PASSWORD": "secret",
            "ALERT_EMAIL_FROM": "bot@example.com", "ALERT_EMAIL_TO": "user@example.com",
        }
        sent = send_email_alert(
            {"event": "STOP_LOSS", "symbol": "ETHUSDT", "direction": "SHORT", "price": 3500, "level": 3550},
            environ=env, smtp_factory=factory,
        )
        self.assertTrue(sent)
        self.assertEqual(len(instances), 1)
        self.assertTrue(instances[0].started_tls)
        self.assertEqual(instances[0].logged_in, ("bot@example.com", "secret"))
        self.assertIsInstance(instances[0].message, EmailMessage)
        self.assertIn("Stop-loss level reached", instances[0].message.get_content())

    def test_invalid_smtp_port_fails_closed(self):
        env = {
            "SMTP_HOST": "smtp.example.com", "SMTP_PORT": "70000",
            "SMTP_PASSWORD": "secret", "ALERT_EMAIL_FROM": "bot@example.com",
            "ALERT_EMAIL_TO": "user@example.com",
        }
        with self.assertRaises(ValueError):
            send_email_alert({"event": "ENTRY_CONFIRMED"}, environ=env, smtp_factory=FakeSMTP)


if __name__ == "__main__":
    unittest.main()
