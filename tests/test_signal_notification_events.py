import unittest

from validation.signal_notification_events import build_signal_notification_event


class SignalNotificationEventTests(unittest.TestCase):
    def test_all_lifecycle_events_have_stable_unique_ids(self):
        cases = [
            ("ENTRY_CONFIRMED", "ENTRY"),
            ("TAKE_PROFIT_1", "TP1"),
            ("TAKE_PROFIT_2", "TP2"),
            ("STOP_LOSS", "STOP_LOSS"),
            ("SIGNAL_INVALIDATED", "SIGNAL_INVALIDATED"),
        ]
        events = [
            build_signal_notification_event(
                event, symbol="BTCUSDT", direction="LONG", signal_time=1700000000000,
                price=65000, entry=64900, sl=64000, tp1=66000, tp2=67000,
                level=64000, score=87,
            )
            for event, _ in cases
        ]
        self.assertEqual(
            [item["event_id"] for item in events],
            [f"BTCUSDT:1700000000000:{suffix}" for _, suffix in cases],
        )
        self.assertEqual(len({item["event_id"] for item in events}), len(cases))
        self.assertEqual([item["event"] for item in events], [name for name, _ in cases])

    def test_short_direction_uses_same_stable_identity_convention(self):
        event = build_signal_notification_event(
            "TAKE_PROFIT_1", symbol="ETHUSDT", direction="SHORT",
            signal_time=1700000000000, price=3000, level=3100,
        )
        self.assertEqual(event["event_id"], "ETHUSDT:1700000000000:TP1")
        self.assertEqual(event["direction"], "SHORT")

    def test_invalid_event_identity_and_direction_fail_closed(self):
        with self.assertRaises(ValueError):
            build_signal_notification_event(
                "ORDER_SUBMITTED", symbol="BTCUSDT", direction="LONG", signal_time=1
            )
        with self.assertRaises(ValueError):
            build_signal_notification_event(
                "ENTRY_CONFIRMED", symbol="BTCUSDT", direction="BUY", signal_time=1
            )
        with self.assertRaises(ValueError):
            build_signal_notification_event(
                "ENTRY_CONFIRMED", symbol="BTCUSDT", direction="LONG", signal_time=0
            )


if __name__ == "__main__":
    unittest.main()
