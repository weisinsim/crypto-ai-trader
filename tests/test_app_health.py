import unittest

from validation.dashboard_health import build_health_payload


SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def healthy_cache():
    return {
        symbol: {"price": 100.0, "ts": 10_000.0, "analysis_ts": 9_999.0}
        for symbol in SYMBOLS
    }


class DashboardHealthGateTests(unittest.TestCase):
    def test_all_symbols_fresh_and_analyzed_is_healthy(self):
        result = build_health_payload(10_030.0, healthy_cache(), SYMBOLS, "RUNNING", "binance")
        self.assertTrue(result["ok"])
        self.assertEqual(result["symbols_ready"], 3)
        self.assertEqual(result["analysis_ready"], 3)
        self.assertEqual(result["stale_symbols"], [])
        self.assertEqual(result["age_sec"], 30.0)

    def test_partial_symbol_data_fails_closed(self):
        data = healthy_cache()
        data["SOLUSDT"]["price"] = None
        result = build_health_payload(10_030.0, data, SYMBOLS, "RUNNING", "binance")
        self.assertFalse(result["ok"])
        self.assertEqual(result["symbols_ready"], 2)

    def test_stale_symbol_is_reported_even_if_other_symbols_are_fresh(self):
        data = healthy_cache()
        data["ETHUSDT"]["ts"] = 9_000.0
        result = build_health_payload(10_030.0, data, SYMBOLS, "RUNNING", "binance")
        self.assertFalse(result["ok"])
        self.assertIn("ETHUSDT", result["stale_symbols"])
        self.assertEqual(result["age_sec"], 1_030.0)

    def test_missing_timestamp_fails_closed(self):
        data = healthy_cache()
        data["BTCUSDT"]["ts"] = 0
        result = build_health_payload(10_030.0, data, SYMBOLS, "RUNNING", "binance")
        self.assertFalse(result["ok"])
        self.assertIn("BTCUSDT", result["stale_symbols"])
        self.assertIsNone(result["symbol_age_sec"]["BTCUSDT"])

    def test_analysis_not_completed_fails_closed(self):
        data = healthy_cache()
        data["AVAXUSDT"] = {"price": 100.0, "ts": 10_000.0, "analysis_ts": 0}
        symbols = SYMBOLS + ["AVAXUSDT"]
        result = build_health_payload(10_030.0, data, symbols, "RUNNING", "binance")
        self.assertFalse(result["ok"])
        self.assertEqual(result["analysis_ready"], 3)

    def test_malformed_cache_records_fail_closed_without_crashing(self):
        data = healthy_cache()
        data["BTCUSDT"]["ts"] = "not-a-timestamp"
        data["ETHUSDT"]["price"] = float("nan")
        data["SOLUSDT"]["analysis_ts"] = "not-an-analysis-time"
        result = build_health_payload(10_030.0, data, SYMBOLS, "RUNNING", "binance")
        self.assertFalse(result["ok"])
        self.assertEqual(result["symbols_ready"], 2)
        self.assertEqual(result["analysis_ready"], 2)
        self.assertIn("BTCUSDT", result["stale_symbols"])

    def test_missing_symbol_record_fails_closed_without_crashing(self):
        data = healthy_cache()
        del data["ETHUSDT"]
        result = build_health_payload(10_030.0, data, SYMBOLS, "RUNNING", "binance")
        self.assertFalse(result["ok"])
        self.assertIn("ETHUSDT", result["stale_symbols"])
        self.assertEqual(result["symbols_ready"], 2)

    def test_staleness_uses_raw_age_before_display_rounding(self):
        data = healthy_cache()
        data["BTCUSDT"]["ts"] = 9_849.96
        result = build_health_payload(10_030.0, data, SYMBOLS, "RUNNING", "binance")
        self.assertFalse(result["ok"])
        self.assertIn("BTCUSDT", result["stale_symbols"])

    def test_invalid_now_fails_closed(self):
        result = build_health_payload(float("nan"), healthy_cache(), SYMBOLS, "RUNNING", "binance")
        self.assertFalse(result["ok"])
        self.assertEqual(result["stale_symbols"], SYMBOLS)

    def test_exact_staleness_threshold_is_allowed(self):
        data = healthy_cache()
        data["BTCUSDT"]["ts"] = 9_850.0
        result = build_health_payload(10_030.0, data, SYMBOLS, "RUNNING", "binance")
        self.assertTrue(result["ok"])
        self.assertEqual(result["symbol_age_sec"]["BTCUSDT"], 180.0)

    def test_staleness_threshold_plus_point_one_seconds_fails(self):
        data = healthy_cache()
        data["BTCUSDT"]["ts"] = 9_849.9
        result = build_health_payload(10_030.0, data, SYMBOLS, "RUNNING", "binance")
        self.assertFalse(result["ok"])
        self.assertIn("BTCUSDT", result["stale_symbols"])


if __name__ == "__main__":
    unittest.main()
