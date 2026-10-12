import json
import unittest

from research.frozen_confirmation import (
    FROZEN_PATH, SOURCE_DATA_END, confirmation_window_indices, sha256, summarize, utc_ms, validate_funding_events,
)


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

    def test_confirmation_window_indices_select_exact_half_open_window(self):
        start = utc_ms("2026-10-02")
        rows = [{"t": start + i * 3_600_000} for i in range(27)]
        self.assertEqual(confirmation_window_indices(rows, start + 3_600_000, start + 25 * 3_600_000), (1, 25))

    def test_confirmation_window_rejects_missing_start_or_gap_at_end(self):
        start = utc_ms("2026-10-02")
        rows = [{"t": start + i * 3_600_000} for i in range(5)]
        with self.assertRaisesRegex(ValueError, "exact confirmation start"):
            confirmation_window_indices(rows, start + 30 * 60_000, start + 4 * 3_600_000)
        gapped = [row for row in rows if row["t"] != start + 2 * 3_600_000]
        with self.assertRaises(ValueError):
            confirmation_window_indices(gapped, start, start + 4 * 3_600_000)

    def test_confirmation_window_rejects_any_internal_hour_gap(self):
        start = utc_ms("2026-10-02")
        rows = [{"t": start + i * 3_600_000} for i in range(6)]
        rows.pop(2)
        with self.assertRaisesRegex(ValueError, "missing or misaligned hourly interval"):
            confirmation_window_indices(rows, start, start + 5 * 3_600_000)

    def test_confirmation_window_rejects_empty_rows(self):
        start = utc_ms("2026-10-02")
        with self.assertRaisesRegex(ValueError, "does not cover confirmation start"):
            confirmation_window_indices([], start, start + 3_600_000)

    def test_confirmation_window_rejects_duplicate_timestamps_and_invalid_range(self):
        start = utc_ms("2026-10-02")
        rows = [{"t": start + i * 3_600_000} for i in range(5)]
        with self.assertRaisesRegex(ValueError, "duplicate candle"):
            confirmation_window_indices(rows + [rows[0]], start, start + 4 * 3_600_000)
        with self.assertRaisesRegex(ValueError, "invalid confirmation window"):
            confirmation_window_indices(rows, start, start)

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

    def test_funding_loader_preserves_venue_for_confirmation_audit(self):
        import csv
        import tempfile
        from research.run_backtest import load_funding_csv

        with tempfile.NamedTemporaryFile(mode="w", newline="", encoding="utf-8", suffix=".csv") as handle:
            writer = csv.DictWriter(handle, fieldnames=(
                "funding_time", "funding_rate", "mark_price", "symbol", "mark_price_source", "venue"
            ))
            writer.writeheader()
            writer.writerow({
                "funding_time": 1_791_820_800_000, "funding_rate": 0.0001,
                "mark_price": 100.0, "symbol": "BTCUSDT",
                "mark_price_source": "okx_mark_price_kline_open", "venue": "okx"
            })
            handle.flush()
            loaded = load_funding_csv(handle.name)
        self.assertEqual(loaded[0]["venue"], "okx")

    def test_funding_event_validation_accepts_archive_labels_and_rejects_proxy(self):
        valid_time = 1_791_820_800_000
        source_labels = ["mark_kline_open", "okx_mark_price_kline_open"]
        events = [
            {"funding_time": valid_time + i * 28_800_000, "symbol": "BTCUSDT",
             "mark_price_source": source}
            for i, source in enumerate(source_labels)
        ]
        events[1]["venue"] = "okx"
        summary = validate_funding_events(events, "BTCUSDT", valid_time, valid_time + 3 * 28_800_000)
        self.assertEqual(summary["records_in_window"], 2)
        self.assertEqual(summary["mark_price_sources"]["mark_kline_open"], 1)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            validate_funding_events([events[0], events[0]], "BTCUSDT", valid_time, valid_time + 28_800_000)
        for ambiguous in ("rest_mark_price", "exchange_funding_mark_price"):
            legacy = {**events[0], "mark_price_source": ambiguous}
            with self.subTest(source=ambiguous), self.assertRaisesRegex(ValueError, "unverified"):
                validate_funding_events([legacy], "BTCUSDT", valid_time, valid_time + 28_800_000)
        proxy = {**events[0], "mark_price_source": "prior_trade_kline_close_proxy"}
        with self.assertRaisesRegex(ValueError, "unverified"):
            validate_funding_events([proxy], "BTCUSDT", valid_time, valid_time + 28_800_000)
        nearest = {**events[0], "mark_price_source": "mark_kline_open_nearest_1s"}
        with self.assertRaisesRegex(ValueError, "unverified"):
            validate_funding_events([nearest], "BTCUSDT", valid_time, valid_time + 28_800_000)
        okx_missing_venue = {**events[1]}
        okx_missing_venue.pop("venue")
        with self.assertRaisesRegex(ValueError, "explicit venue=okx"):
            validate_funding_events([okx_missing_venue], "BTCUSDT", valid_time, valid_time + 28_800_000)
        okx_conflicting_venue = {**events[1], "venue": "binance"}
        with self.assertRaisesRegex(ValueError, "explicit venue=okx"):
            validate_funding_events([okx_conflicting_venue], "BTCUSDT", valid_time, valid_time + 28_800_000)

        binance_event = {
            **events[0],
            "mark_price_source": "binance_fapi_funding_rate_mark_price",
            "venue": "binance",
        }
        self.assertEqual(
            validate_funding_events([binance_event], "BTCUSDT", valid_time, valid_time + 28_800_000)["records_in_window"],
            1,
        )
        for bad_venue in ("", "okx"):
            invalid_binance = {**binance_event, "venue": bad_venue}
            with self.subTest(binance_venue=bad_venue), self.assertRaisesRegex(
                ValueError, "explicit venue=binance"
            ):
                validate_funding_events(
                    [invalid_binance], "BTCUSDT", valid_time, valid_time + 28_800_000
                )
        with self.assertRaisesRegex(ValueError, "symbol"):
            validate_funding_events([events[0]], "AVAXUSDT", valid_time, valid_time + 28_800_000)
        misaligned = {**events[0], "funding_time": valid_time + 1}
        with self.assertRaisesRegex(ValueError, "aligned"):
            validate_funding_events([misaligned], "BTCUSDT", valid_time, valid_time + 28_800_000)
        with self.assertRaisesRegex(ValueError, "no funding events"):
            validate_funding_events([], "BTCUSDT", valid_time, valid_time + 28_800_000)
        with self.assertRaisesRegex(ValueError, "window"):
            validate_funding_events(events, "BTCUSDT", valid_time, valid_time)
        with self.assertRaisesRegex(ValueError, "non-empty string"):
            validate_funding_events(events, "", valid_time, valid_time + 3 * 28_800_000)


if __name__ == "__main__":
    unittest.main()
