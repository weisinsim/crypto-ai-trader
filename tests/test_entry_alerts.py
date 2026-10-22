"""Tests for actionable entry-alert event construction and deduplication."""
import unittest

from execution.entry_alerts import build_entry_alert, should_emit_alert


class EntryAlertTests(unittest.TestCase):
    def base(self):
        return {
            "symbol": "BTCUSDT", "signal": "LONG", "score": 82,
            "score_label": "A", "model_status": "RESEARCH",
            "analysis_ts": 1_800_000_000, "last_closed_candle": 1_799_996_400_000,
            "price": 65000.0, "entry": 65000.0, "sl": 64500.0,
            "tp1": 66000.0, "tp2": 66500.0, "rr": 2.0,
            "source": "Binance Futures",
        }

    def test_builds_candidate_alert_for_valid_closed_candle(self):
        event = build_entry_alert(self.base(), now_ms=1_800_000_100_000)
        self.assertTrue(event["eligible"])
        self.assertTrue(event["research_only"])
        self.assertEqual(event["side"], "LONG")
        self.assertEqual(event["symbol"], "BTCUSDT")
        self.assertEqual(event["risk_reward"], 2.0)
        self.assertFalse(event["auto_trade_authorized"])

    def test_noncanonical_symbols_are_rejected(self):
        for symbol in ("btcusdt", "BTC/USDT", "BTC USDT", "BTCUSDT.evil", "UUSDT"):
            with self.subTest(symbol=symbol):
                data = self.base()
                data["symbol"] = symbol
                event = build_entry_alert(data, now_ms=1_800_000_100_000)
                self.assertFalse(event["eligible"])
                self.assertIn("invalid_symbol", event["blockers"])

    def test_watch_signal_is_not_actionable_alert(self):
        data = self.base()
        data["signal"] = "WATCH-LONG"
        self.assertFalse(build_entry_alert(data, now_ms=1_800_000_100_000)["eligible"])

    def test_no_production_model_is_blocked(self):
        data = self.base()
        data["model_status"] = "NO-PRODUCTION"
        event = build_entry_alert(data, now_ms=1_800_000_100_000)
        self.assertFalse(event["eligible"])
        self.assertIn("model_not_approved_for_alerts", event["blockers"])

    def test_stale_signal_fails_closed(self):
        event = build_entry_alert(self.base(), now_ms=1_800_010_000_000, max_age_ms=60_000)
        self.assertFalse(event["eligible"])
        self.assertIn("stale_signal", event["blockers"])

    def test_invalid_geometry_fails_closed(self):
        data = self.base()
        data["sl"] = 66000.0
        event = build_entry_alert(data, now_ms=1_800_000_100_000)
        self.assertFalse(event["eligible"])
        self.assertIn("invalid_stop_geometry", event["blockers"])

    def test_missing_closed_candle_timestamp_fails_closed(self):
        data = self.base()
        data["last_closed_candle"] = None
        event = build_entry_alert(data, now_ms=1_800_000_100_000)
        self.assertFalse(event["eligible"])
        self.assertIn("missing_closed_candle", event["blockers"])

    def test_dedupes_same_symbol_side_candle(self):
        a = build_entry_alert(self.base(), now_ms=1_800_000_100_000)
        b = dict(a)
        self.assertTrue(should_emit_alert(a, set()))
        self.assertFalse(should_emit_alert(b, {a["event_id"]}))

    def test_invalid_time_window_fails_closed(self):
        for now_ms, max_age_ms in (
            (float("nan"), 5_000),
            (float("inf"), 5_000),
            (True, 5_000),
            (1_800_000_100_000, float("nan")),
            (1_800_000_100_000, float("inf")),
            (1_800_000_100_000, -1),
        ):
            with self.subTest(now_ms=now_ms, max_age_ms=max_age_ms):
                event = build_entry_alert(
                    self.base(), now_ms=now_ms, max_age_ms=max_age_ms)
                self.assertFalse(event["eligible"])
                self.assertIn("invalid_time_window", event["blockers"])

    def test_malformed_direction_and_model_status_fail_closed(self):
        for field, value in (("signal", ["LONG"]), ("model_status", ["PRODUCTION"])):
            with self.subTest(field=field):
                data = self.base()
                data[field] = value
                event = build_entry_alert(data, now_ms=1_800_000_100_000)
                self.assertFalse(event["eligible"])
                expected = ("not_actionable_direction" if field == "signal"
                            else "model_not_approved_for_alerts")
                self.assertIn(expected, event["blockers"])

    def test_numeric_nan_is_rejected(self):
        data = self.base()
        data["entry"] = float("nan")
        event = build_entry_alert(data, now_ms=1_800_000_100_000)
        self.assertFalse(event["eligible"])
        self.assertIn("invalid_numeric_fields", event["blockers"])


if __name__ == "__main__":
    unittest.main()
