"""Tests for Telegram delivery adapter with a mocked transport."""
import json
import unittest

from execution.telegram_notifier import (
    NotificationDeliveryError,
    send_telegram_alert,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class TelegramNotifierTests(unittest.TestCase):
    def event(self):
        return {
            "eligible": True, "symbol": "BTCUSDT", "side": "LONG",
            "research_only": True, "score": 80, "entry": 100,
            "stop_loss": 95, "take_profit_1": 110, "take_profit_2": 115,
            "signal_candle_ms": 1_800_000_000_000,
        }

    def test_sends_formatted_alert_and_returns_receipt(self):
        captured = {}

        def opener(request, timeout):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            captured["body"] = request.data.decode()
            return FakeResponse({"ok": True, "result": {"message_id": 123}})

        result = send_telegram_alert(
            self.event(), token="test-token", chat_id="42", opener=opener
        )
        self.assertTrue(result["delivered"])
        self.assertEqual(result["message_id"], 123)
        self.assertIn("api.telegram.org", captured["url"])
        self.assertIn("NOT LIVE-APPROVED", captured["body"])

    def test_missing_credentials_fail_without_network(self):
        called = []
        with self.assertRaises(NotificationDeliveryError):
            send_telegram_alert(self.event(), token="", chat_id="42", opener=lambda *a, **k: called.append(True))
        self.assertEqual(called, [])

    def test_provider_rejection_fails_closed(self):
        with self.assertRaises(NotificationDeliveryError):
            send_telegram_alert(
                self.event(), token="test-token", chat_id="42",
                opener=lambda *a, **k: FakeResponse({"ok": False, "description": "no"}),
            )

    def test_ineligible_event_is_not_sent(self):
        event = self.event()
        event["eligible"] = False
        called = []
        with self.assertRaises(ValueError):
            send_telegram_alert(event, token="test-token", chat_id="42", opener=lambda *a, **k: called.append(True))
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
