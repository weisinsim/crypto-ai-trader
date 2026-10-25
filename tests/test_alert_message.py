"""Tests for notification text formatting."""
import unittest

from execution.alert_message import format_entry_alert


class EntryAlertMessageTests(unittest.TestCase):
    def event(self):
        return {
            "eligible": True, "symbol": "BTCUSDT", "side": "LONG",
            "notification_only": True, "auto_trade_authorized": False,
            "research_only": True, "score": 82, "entry": 65000,
            "stop_loss": 64500, "take_profit_1": 66000, "take_profit_2": 66500,
            "risk_reward": 2.0, "signal_candle_ms": 1_800_000_000_000,
        }

    def test_research_candidate_is_clearly_labeled(self):
        text = format_entry_alert(self.event())
        self.assertIn("BTCUSDT LONG", text)
        self.assertIn("NOT LIVE-APPROVED", text)
        self.assertIn("Stop loss: 64500", text)
        self.assertIn("no order was placed", text)

    def test_non_finite_and_boolean_numbers_are_rendered_as_unavailable(self):
        event = self.event()
        event.update({
            "score": float("nan"),
            "entry": float("inf"),
            "stop_loss": True,
            "take_profit_1": 10 ** 10000,
            "take_profit_2": float("-inf"),
            "risk_reward": float("nan"),
            "signal_candle_ms": float("inf"),
        })
        text = format_entry_alert(event)
        self.assertIn("Score: n/a", text)
        self.assertIn("Entry: n/a", text)
        self.assertIn("Stop loss: n/a", text)
        self.assertIn("Take profit 1: n/a", text)
        self.assertIn("Take profit 2: n/a", text)
        self.assertIn("Signal candle: n/a", text)
        self.assertNotIn("Risk/reward:", text)
        self.assertNotIn("nan", text.lower())
        self.assertNotIn("inf", text.lower())

    def test_rejects_malformed_symbol_and_direction(self):
        for field, value in (("symbol", "BTCUSDT\\nFAKE ALERT"), ("symbol", "btc/usdt"),
                             ("side", "LONG\\nEXECUTE"), ("side", ["LONG"])):
            with self.subTest(field=field, value=value):
                event = self.event()
                event[field] = value
                with self.assertRaises(ValueError):
                    format_entry_alert(event)

    def test_rejects_ineligible_events(self):
        event = self.event()
        event["eligible"] = False
        with self.assertRaises(ValueError):
            format_entry_alert(event)

    def test_rejects_non_notification_event(self):
        event = self.event()
        event["notification_only"] = False
        with self.assertRaises(ValueError):
            format_entry_alert(event)

    def test_rejects_execution_authorized_event(self):
        event = self.event()
        event["auto_trade_authorized"] = True
        with self.assertRaises(ValueError):
            format_entry_alert(event)

    def test_research_only_false_does_not_authorize_production_label(self):
        event = self.event()
        event["research_only"] = False
        self.assertIn("NOT LIVE-APPROVED", format_entry_alert(event))
        self.assertNotIn("PRODUCTION SIGNAL", format_entry_alert(event))

    def test_production_label_requires_explicit_release_approval(self):
        event = self.event()
        event.update({
            "research_only": False,
            "model_status": "PRODUCTION",
            "production_approval": True,
            "notification_only": True,
            "auto_trade_authorized": False,
        })
        self.assertIn("PRODUCTION SIGNAL", format_entry_alert(event))


if __name__ == "__main__":
    unittest.main()
