import unittest

from validation.alert_contract import build_alert_payload, validate_alert_candidate


class AlertContractTests(unittest.TestCase):
    def setUp(self):
        self.now = 1_800_000_000.0
        self.item = {
            "confirmed_signal": "LONG",
            "model_status": "RESEARCH",
            "ts": self.now - 5,
            "price": 100.0,
            "confirmed_signal_time": (self.now - 60) * 1000,
            "analysis_ts": self.now - 30,
            "confirmed_entry": 100.0,
            "confirmed_sl": 98.0,
            "confirmed_tp1": 103.0,
            "confirmed_tp2": 106.0,
            "confirmed_score": 75.0,
            "confirmed_score_label": "GOOD",
            "model_name": "Unit Test Model",
        }

    def test_valid_candidate_is_notification_only(self):
        alert = validate_alert_candidate("BTCUSDT", self.item, self.now)
        self.assertIsNotNone(alert)
        self.assertTrue(alert["notification_only"])
        self.assertIs(alert["auto_trade_authorized"], False)
        self.assertTrue(alert["research_only"])
        self.assertEqual(alert["side"], "LONG")

    def test_malformed_symbols_are_rejected(self):
        for symbol in ("BTC/USDT", "btcUSDT", "USDT", "A" * 21 + "USDT", "BTC-USDT", None):
            with self.subTest(symbol=symbol):
                self.assertIsNone(validate_alert_candidate(symbol, self.item, self.now))

    def test_frozen_or_unknown_model_is_rejected(self):
        for status in ("FROZEN", "UNKNOWN", None, "NO-PRODUCTION"):
            with self.subTest(status=status):
                item = {**self.item, "model_status": status}
                self.assertIsNone(validate_alert_candidate("BTCUSDT", item, self.now))

    def test_stale_or_future_ticker_is_rejected(self):
        for ts in (self.now - 121, self.now + 1, True, float("nan")):
            with self.subTest(ts=ts):
                item = {**self.item, "ts": ts}
                self.assertIsNone(validate_alert_candidate("BTCUSDT", item, self.now))

    def test_invalid_prices_and_boolean_values_are_rejected(self):
        for field, value in (
            ("price", float("inf")),
            ("confirmed_entry", True),
            ("confirmed_sl", float("nan")),
            ("confirmed_tp1", 0),
            ("confirmed_score", True),
        ):
            with self.subTest(field=field):
                item = {**self.item, field: value}
                self.assertIsNone(validate_alert_candidate("BTCUSDT", item, self.now))

    def test_wrong_long_geometry_is_rejected(self):
        item = {**self.item, "confirmed_sl": 101.0}
        self.assertIsNone(validate_alert_candidate("BTCUSDT", item, self.now))

    def test_short_geometry_is_checked(self):
        item = {
            **self.item,
            "confirmed_signal": "SHORT",
            "confirmed_entry": 100.0,
            "confirmed_sl": 102.0,
            "confirmed_tp1": 97.0,
            "confirmed_tp2": 94.0,
        }
        self.assertIsNotNone(validate_alert_candidate("BTCUSDT", item, self.now))
        item["confirmed_tp2"] = 103.0
        self.assertIsNone(validate_alert_candidate("BTCUSDT", item, self.now))

    def test_stale_analysis_or_candle_is_rejected(self):
        for field, value in (
            ("analysis_ts", self.now - 5401),
            ("confirmed_signal_time", (self.now - 5401) * 1000),
            ("analysis_ts", self.now + 1),
        ):
            with self.subTest(field=field, value=value):
                item = {**self.item, field: value}
                self.assertIsNone(validate_alert_candidate("BTCUSDT", item, self.now))

    def test_alert_envelope_fails_closed_for_stale_or_non_live_heartbeat(self):
        for status, updated in (
            ("STARTING", self.now),
            ("LIVE", self.now - 121),
            ("LIVE", self.now + 1),
            ("LIVE", True),
        ):
            with self.subTest(status=status, updated=updated):
                payload = build_alert_payload(
                    {"status": status, "updated": updated},
                    {"BTCUSDT": self.item},
                    ["BTCUSDT"],
                    self.now,
                )
                self.assertEqual(payload["status"], "DELAYED")
                self.assertEqual(payload["alerts"], [])
                self.assertFalse(payload["auto_trade_authorized"])

    def test_alert_envelope_includes_valid_signal_only_with_fresh_live_heartbeat(self):
        payload = build_alert_payload(
            {"status": "LIVE", "updated": self.now - 2},
            {"BTCUSDT": self.item},
            ["BTCUSDT"],
            self.now,
        )
        self.assertEqual(payload["status"], "LIVE")
        self.assertEqual(len(payload["alerts"]), 1)
        self.assertTrue(payload["notification_only"])
        self.assertIs(payload["auto_trade_authorized"], False)

    def test_non_dict_cache_and_invalid_clock_fail_closed(self):
        payload = build_alert_payload(
            {"status": "LIVE", "updated": self.now - 2},
            None,
            ["BTCUSDT"],
            self.now,
        )
        self.assertEqual(payload["status"], "LIVE")
        self.assertEqual(payload["alerts"], [])
        payload = build_alert_payload({"status": "LIVE"}, {}, ["BTCUSDT"], float("nan"))
        self.assertEqual(payload["status"], "DELAYED")
        self.assertEqual(payload["alerts"], [])
        self.assertFalse(payload["auto_trade_authorized"])

    def test_production_status_is_not_marked_research_only(self):
        item = {**self.item, "model_status": "PRODUCTION"}
        alert = validate_alert_candidate("BTCUSDT", item, self.now)
        self.assertIsNotNone(alert)
        self.assertFalse(alert["research_only"])


if __name__ == "__main__":
    unittest.main()
