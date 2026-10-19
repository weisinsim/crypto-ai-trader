"""Tests for notification text formatting."""
import unittest

from execution.alert_message import format_entry_alert


class EntryAlertMessageTests(unittest.TestCase):
    def event(self):
        return {
            "eligible": True, "symbol": "BTCUSDT", "side": "LONG",
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

    def test_rejects_ineligible_events(self):
        event = self.event()
        event["eligible"] = False
        with self.assertRaises(ValueError):
            format_entry_alert(event)

    def test_production_label_only_when_flag_is_false(self):
        event = self.event()
        event["research_only"] = False
        self.assertIn("PRODUCTION SIGNAL", format_entry_alert(event))


if __name__ == "__main__":
    unittest.main()
