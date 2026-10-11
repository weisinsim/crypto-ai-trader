import json
import unittest

from research.frozen_confirmation import FROZEN_PATH, SOURCE_DATA_END, sha256, summarize, utc_ms


class FrozenConfirmationTests(unittest.TestCase):
    def test_frozen_config_has_only_the_two_declared_candidates(self):
        config = json.loads(FROZEN_PATH.read_text(encoding="utf-8"))
        self.assertEqual(set(config["candidates"]), {"BTCUSDT", "AVAXUSDT"})
        self.assertFalse(config["confirmation_policy"]["parameter_search_allowed"])
        self.assertFalse(config["confirmation_policy"]["live_trading_allowed"])

    def test_frozen_config_hash_is_stable_for_current_file(self):
        digest = sha256(FROZEN_PATH)
        self.assertEqual(len(digest), 64)
        self.assertTrue(all(char in "0123456789abcdef" for char in digest))

    def test_confirmation_window_boundary_is_explicit_utc(self):
        self.assertEqual(utc_ms("2026-10-01"), int(SOURCE_DATA_END.timestamp() * 1000))
        self.assertLess(utc_ms("2026-10-01"), utc_ms("2026-10-02"))

    def test_summarize_reports_funding_cashflow(self):
        report = summarize({
            "trades": 2,
            "net_return_pct": 1.2,
            "max_drawdown_pct": 0.4,
            "win_rate_pct": 50,
            "profit_factor": 1.4,
            "trade_log": [
                {"pnl_equity": 0.01, "funding_pnl_equity": 0.001, "funding_events": 1},
                {"pnl_equity": -0.002, "funding_pnl_equity": -0.0002, "funding_events": 1},
            ],
        })
        self.assertAlmostEqual(report["funding_pnl_equity"], 0.0008)
        self.assertEqual(report["funding_events"], 2)

    def test_funding_off_counterfactual_delta(self):
        with_funding = {"net_return_pct": 2.0}
        without_funding = {"net_return_pct": 1.5}
        self.assertEqual(round(with_funding["net_return_pct"] - without_funding["net_return_pct"], 8), 0.5)

    def test_funding_event_validation_rejects_duplicate_and_proxy_sources(self):
        def validate(events):
            times = [int(event["funding_time"]) for event in events]
            if len(times) != len(set(times)):
                raise ValueError("duplicate funding settlement timestamps detected")
            if any(t % 28_800_000 != 0 for t in times):
                raise ValueError("funding settlement timestamp is not aligned")
            sources = {event.get("mark_price_source", "unspecified") for event in events}
            allowed = {
                "mark_kline_open", "mark_kline_open_nearest_1s",
                "mark_price_kline_open", "exchange_funding_mark_price", "rest_mark_price",
            }
            if sources - allowed:
                raise ValueError("unverified funding mark-price provenance")
        valid_time = 1_791_820_800_000
        valid = {"funding_time": valid_time, "mark_price_source": "mark_price_kline_open"}
        with self.assertRaises(ValueError):
            validate([valid, valid.copy()])
        with self.assertRaises(ValueError):
            validate([{"funding_time": valid_time, "mark_price_source": "prior_trade_kline_close_proxy"}])


if __name__ == "__main__":
    unittest.main()
