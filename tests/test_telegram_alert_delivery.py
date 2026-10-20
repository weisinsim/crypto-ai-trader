"""Unit tests for opt-in Telegram alert delivery; all HTTP is mocked."""
import json
import unittest
from unittest.mock import patch

from execution import telegram_alert_delivery as delivery


def event(**extra):
    value = {
        "event_id": "evt-1",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "eligible": True,
        "notification_only": True,
        "auto_trade_authorized": False,
        "entry_price": 100.0,
        "stop_loss": 95.0,
        "take_profit": 110.0,
        "risk_reward": 2.0,
    }
    value.update(extra)
    return value


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class TelegramDeliveryTests(unittest.TestCase):
    def test_formats_only_eligible_notification_events(self):
        text = delivery.format_alert(event())
        self.assertIn("BTCUSDT | LONG", text)
        self.assertIn("NOT AN ORDER", text)
        with self.assertRaisesRegex(ValueError, "eligible"):
            delivery.format_alert(event(eligible=False))
        with self.assertRaisesRegex(ValueError, "notification-only"):
            delivery.format_alert(event(auto_trade_authorized=True))
        with self.assertRaisesRegex(ValueError, "finite numeric"):
            delivery.format_alert(event(entry_price=float("nan")))

    def test_sender_uses_json_and_requires_confirmed_api_response(self):
        seen = {}

        def opener(request, timeout):
            seen["url"] = request.full_url
            seen["body"] = json.loads(request.data)
            seen["timeout"] = timeout
            return Response({"ok": True, "result": {"message_id": 42}})

        result = delivery.send_telegram_message("hello", token="test-token", chat_id="123", opener=opener)
        self.assertEqual(result, 42)
        self.assertTrue(seen["url"].startswith("https://api.telegram.org/"))
        self.assertEqual(seen["body"], {"chat_id": "123", "text": "hello"})
        with self.assertRaisesRegex(RuntimeError, "delivery_rejected"):
            delivery.send_telegram_message("hello", token="test-token", chat_id="123",
                                           opener=lambda *a, **k: Response({"ok": False}))

    def test_delivery_marks_only_confirmed_success(self):
        class Store:
            def __init__(self):
                self.marked = []
            def pending(self, limit=100):
                return [{"event_id": "evt-1", "event": event()}]
            def mark_delivered(self, event_id):
                self.marked.append(event_id)
                return True

        store = Store()
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "token", "TELEGRAM_CHAT_ID": "chat"}):
            result = delivery.deliver_pending(store, sender=lambda *a, **k: 42, emit=lambda _line: None)
        self.assertEqual(result, {"sent": 1, "failed": 0})
        self.assertEqual(store.marked, ["evt-1"])

    def test_missing_credentials_fail_closed(self):
        class Store:
            def pending(self, limit=100):
                self.fail("must not read outbox without credentials")
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""}):
            with self.assertRaisesRegex(RuntimeError, "credentials_not_configured"):
                delivery.deliver_pending(Store())


if __name__ == "__main__":
    unittest.main()
