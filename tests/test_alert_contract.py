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

    def test_candidate_score_threshold_is_70_and_not_a_probability_claim(self):
        from validation.alert_contract import MIN_ENTRY_CANDIDATE_SCORE

        self.assertEqual(MIN_ENTRY_CANDIDATE_SCORE, 70.0)
        for score in (0, 59.9, 60, 69.99):
            with self.subTest(score=score):
                self.assertIsNone(
                    validate_alert_candidate(
                        "BTCUSDT", {**self.item, "confirmed_score": score}, self.now
                    )
                )

        alert = validate_alert_candidate(
            "BTCUSDT", {**self.item, "confirmed_score": 70.0}, self.now
        )
        self.assertIsNotNone(alert)
        self.assertTrue(alert["entry_candidate"])
        self.assertIs(alert["score_is_probability"], False)
        self.assertEqual(alert["minimum_candidate_score"], 70.0)
        self.assertIs(alert["auto_trade_authorized"], False)

    def test_high_score_never_overrides_notification_only_safety_boundary(self):
        alert = validate_alert_candidate(
            "BTCUSDT", {**self.item, "confirmed_score": 100.0}, self.now
        )
        self.assertIsNotNone(alert)
        self.assertIs(alert["auto_trade_authorized"], False)
        self.assertTrue(alert["notification_only"])

    def test_malformed_symbols_are_rejected(self):
        for symbol in ("BTC/USDT", "btcUSDT", "USDT", "A" * 21 + "USDT", "BTC-USDT", None):
            with self.subTest(symbol=symbol):
                self.assertIsNone(validate_alert_candidate(symbol, self.item, self.now))

    def test_frozen_or_unknown_model_is_rejected(self):
        for status in ("FROZEN", "UNKNOWN", None, "NO-PRODUCTION", [], {}):
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
            ([], self.now),
            ({}, self.now),
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

    def test_duplicate_symbols_do_not_duplicate_same_event(self):
        payload = build_alert_payload(
            {"status": "LIVE", "updated": self.now - 2},
            {"BTCUSDT": self.item},
            ["BTCUSDT", "BTCUSDT"],
            self.now,
        )
        self.assertEqual(len(payload["alerts"]), 1)
        self.assertEqual(payload["alerts"][0]["symbol"], "BTCUSDT")

    def test_malformed_symbol_entries_and_status_types_fail_closed(self):
        for status in ([], {}, 123):
            with self.subTest(status=status):
                item = {**self.item, "model_status": status}
                self.assertIsNone(validate_alert_candidate("BTCUSDT", item, self.now))

        payload = build_alert_payload(
            {"status": "LIVE", "updated": self.now - 2},
            {"BTCUSDT": self.item},
            [["BTCUSDT"], {}, "BTCUSDT"],
            self.now,
        )
        self.assertEqual(payload["status"], "LIVE")
        self.assertEqual([a["symbol"] for a in payload["alerts"]], ["BTCUSDT"])
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
        for updated in ([], {}, "1800000000"):
            with self.subTest(updated=updated):
                payload = build_alert_payload(
                    {"status": "LIVE", "updated": updated},
                    {"BTCUSDT": self.item},
                    ["BTCUSDT"],
                    self.now,
                )
                self.assertEqual(payload["status"], "DELAYED")
                self.assertEqual(payload["alerts"], [])
        self.assertEqual(payload["alerts"], [])
        self.assertFalse(payload["auto_trade_authorized"])


    def test_invalid_max_age_windows_fail_closed(self):
        for age in (0, -1, True, float("nan"), float("inf")):
            with self.subTest(age=age):
                self.assertIsNone(
                    validate_alert_candidate("BTCUSDT", self.item, self.now, max_age_seconds=age)
                )

    def test_untrusted_metadata_is_bounded_and_string_only(self):
        item = {
            **self.item,
            "model_name": "<script>" + ("x" * 200),
            "confirmed_score_label": {"unexpected": "object"},
        }
        alert = validate_alert_candidate("BTCUSDT", item, self.now)
        self.assertIsNotNone(alert)
        self.assertEqual(len(alert["model_name"]), 80)
        self.assertTrue(alert["model_name"].startswith("<script>"))
        self.assertIsNone(alert["score_label"])

    def test_production_status_is_not_marked_research_only(self):
        item = {**self.item, "model_status": "PRODUCTION"}
        alert = validate_alert_candidate("BTCUSDT", item, self.now)
        self.assertIsNotNone(alert)
        self.assertFalse(alert["research_only"])


    def test_malformed_signal_field_types_fail_closed(self):
        malformed = (
            ("confirmed_signal", []),
            ("confirmed_signal", {}),
            ("ts", []),
            ("price", {}),
            ("confirmed_signal_time", []),
            ("analysis_ts", {}),
            ("confirmed_entry", []),
            ("confirmed_sl", {}),
            ("confirmed_tp1", []),
            ("confirmed_tp2", {}),
            ("confirmed_score", []),
        )
        for field, value in malformed:
            with self.subTest(field=field, value=value):
                item = {**self.item, field: value}
                self.assertIsNone(validate_alert_candidate("BTCUSDT", item, self.now))

    def test_malformed_envelope_types_fail_closed_without_exception(self):
        for market_status, symbol_cache, symbols in (
            ([], {"BTCUSDT": self.item}, ["BTCUSDT"]),
            ({}, [], ["BTCUSDT"]),
            ({"status": [], "updated": self.now}, {"BTCUSDT": self.item}, ["BTCUSDT"]),
            ({"status": "LIVE", "updated": []}, {"BTCUSDT": self.item}, ["BTCUSDT"]),
            ({"status": "LIVE", "updated": self.now - 2}, {"BTCUSDT": self.item}, {}),
        ):
            with self.subTest(market_status=market_status, symbol_cache=symbol_cache, symbols=symbols):
                payload = build_alert_payload(market_status, symbol_cache, symbols, self.now)
                self.assertIn(payload["status"], ("LIVE", "DELAYED"))
                self.assertEqual(payload["alerts"], [])
                self.assertIs(payload["notification_only"], True)
                self.assertIs(payload["auto_trade_authorized"], False)


if __name__ == "__main__":
    unittest.main()
