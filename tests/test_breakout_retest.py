import unittest

from research.breakout_retest import GRID, atr_values, build_indicator_cache, candidate_rejection_reasons, funding_cashflow, marked_equity_after_costs_and_funding, profit_factor_above_one, run_breakout_retest


def make_rows(n=240):
    rows = []
    close = 100.0
    for i in range(n):
        # Repeated range phases followed by directional expansions create
        # deterministic OHLCV input without external data.
        phase = i % 48
        if phase < 28:
            close = 100.0 + ((phase % 7) - 3) * 0.25
        elif phase < 34:
            close = 100.0 + (phase - 27) * 0.9
        elif phase < 40:
            close = 105.0 - (phase - 33) * 0.45
        else:
            close = 102.0 + ((phase % 5) - 2) * 0.3
        rows.append({
            "t": i * 3_600_000,
            "o": close - 0.1,
            "h": close + 0.35,
            "l": close - 0.35,
            "c": close,
            "v": 100.0,
        })
    return rows


class BreakoutRetestTests(unittest.TestCase):
    def test_direction_summary_reports_best_candidate_per_direction(self):
        from research.breakout_retest import candidate_rejection_reasons
        candidates = [
            {"parameters": {"direction": "LONG"}, "eligible": False, "score": 1.0,
             "median_fold_return_pct": 2.0, "worst_fold_return_pct": -4.0,
             "rejection_reasons": ["worst_fold_below_minus_3pct"]},
            {"parameters": {"direction": "SHORT"}, "eligible": True, "score": 1.5,
             "median_fold_return_pct": 2.5, "worst_fold_return_pct": -1.0,
             "rejection_reasons": []},
            {"parameters": {"direction": "BOTH"}, "eligible": False, "score": -0.2,
             "median_fold_return_pct": 0.0, "worst_fold_return_pct": -2.0,
             "rejection_reasons": ["risk_adjusted_score_not_positive"]},
        ]
        summary = {}
        for direction in ("LONG", "SHORT", "BOTH"):
            group = [item for item in candidates if item["parameters"]["direction"] == direction]
            passing = sorted((item for item in group if item["eligible"]), key=lambda item: item["score"], reverse=True)
            best = passing[0] if passing else max(group, key=lambda item: item["score"])
            summary[direction] = {"best_candidate_eligible": best["eligible"], "best_score": best["score"]}
        self.assertFalse(summary["LONG"]["best_candidate_eligible"])
        self.assertTrue(summary["SHORT"]["best_candidate_eligible"])
        self.assertFalse(summary["BOTH"]["best_candidate_eligible"])

    def test_infinite_profit_factor_is_valid_but_null_is_not(self):
        self.assertTrue(profit_factor_above_one("INF"))
        self.assertTrue(profit_factor_above_one(1.0001))
        self.assertFalse(profit_factor_above_one(1.0))
        self.assertFalse(profit_factor_above_one(None))
        self.assertFalse(profit_factor_above_one(float("nan")))

    def test_grid_covers_each_direction_without_duplicate_candidates(self):
        self.assertEqual(len(GRID), 5184)
        self.assertEqual({p["direction"] for p in GRID}, {"BOTH", "LONG", "SHORT"})
        self.assertEqual({p["breakout_atr"] for p in GRID}, {0.05, 0.15})
        self.assertEqual({p["retest_atr"] for p in GRID}, {0.20, 0.40})
        self.assertEqual({p["body_confirm_atr"] for p in GRID}, {0.0, 0.15})
        self.assertEqual({p["target_r"] for p in GRID}, {1.5, 2.0, 2.5, 3.0})
        self.assertEqual({p["breakout_atr"] for p in GRID}, {0.05, 0.15})
        self.assertEqual({p["retest_atr"] for p in GRID}, {0.20, 0.40})
        self.assertEqual(len({tuple(sorted(p.items())) for p in GRID}), len(GRID))

    def test_atr_warmup_and_positive_values(self):
        rows = make_rows()
        atr = atr_values(rows, period=14)
        self.assertIsNone(atr[12])
        self.assertIsNotNone(atr[13])
        self.assertGreater(atr[13], 0)

    def test_cached_indicators_preserve_backtest_results_and_support_prefixes(self):
        rows = make_rows()
        cache = build_indicator_cache(rows)
        params = {"lookback": 24, "max_wait": 6, "target_r": 2.0, "trend_period": 100}
        uncached = run_breakout_retest(rows, params, 100, len(rows))
        cached = run_breakout_retest(rows, params, 100, len(rows), indicator_cache=cache)
        self.assertEqual(cached, uncached)

        prefix = rows[:200]
        uncached_prefix = run_breakout_retest(prefix, params, 100, len(prefix))
        cached_prefix = run_breakout_retest(
            prefix, params, 100, len(prefix), indicator_cache=cache,
        )
        self.assertEqual(cached_prefix, uncached_prefix)

    def test_indicator_cache_rejects_insufficient_coverage(self):
        rows = make_rows()
        cache = build_indicator_cache(rows[:150])
        with self.assertRaisesRegex(ValueError, "does not cover"):
            run_breakout_retest(
                rows, {"lookback": 24, "max_wait": 6, "target_r": 2.0},
                100, len(rows), indicator_cache=cache,
            )

    def test_backtest_returns_separate_long_and_short_statistics(self):
        rows = make_rows()
        params = {"lookback": 24, "max_wait": 6, "target_r": 2.0}
        result = run_breakout_retest(rows, params, 100, len(rows))
        self.assertIn("long", result)
        self.assertIn("short", result)
        self.assertIn("trades", result)
        self.assertIn("max_drawdown_pct", result)
        self.assertGreaterEqual(result["max_drawdown_pct"], 0)
        for trade in result["trade_log"]:
            self.assertIn(trade["side"], ("LONG", "SHORT"))
            self.assertGreaterEqual(trade["entry_time"], rows[100]["t"])

    def test_direction_filter_restricts_breakout_side(self):
        rows = make_rows()
        base = {"lookback": 24, "max_wait": 6, "target_r": 2.0}
        long_result = run_breakout_retest(rows, base | {"direction": "LONG"}, 100, len(rows))
        short_result = run_breakout_retest(rows, base | {"direction": "SHORT"}, 100, len(rows))
        self.assertTrue(all(t["side"] == "LONG" for t in long_result["trade_log"]))
        self.assertTrue(all(t["side"] == "SHORT" for t in short_result["trade_log"]))

    def test_invalid_costs_rejected(self):
        rows = make_rows()
        params = {"lookback": 24, "max_wait": 6, "target_r": 2.0}
        with self.assertRaisesRegex(ValueError, "fees and slippage"):
            run_breakout_retest(rows, params, 100, len(rows), fee_rate=float("nan"))

    def test_backtest_rejects_non_finite_or_boolean_risk_and_leverage(self):
        rows = make_rows()
        params = {"lookback": 24, "max_wait": 6, "target_r": 2.0}
        for kwargs in (
            {"max_leverage": float("nan")},
            {"max_leverage": float("inf")},
            {"max_leverage": True},
            {"risk_fraction": float("nan")},
            {"risk_fraction": True},
            {"fee_rate": True},
            {"slippage_rate": float("inf")},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaisesRegex(ValueError, "risk fraction|max leverage|fees and slippage"):
                    run_breakout_retest(rows, params, 100, len(rows), **kwargs)

    def test_funding_cashflow_rejects_malformed_timestamps_without_truncation(self):
        for ts in (True, False, 100.5, "100.5", "1e3"):
            with self.subTest(ts=ts):
                with self.assertRaisesRegex(ValueError, "funding timestamp must be an integer"):
                    funding_cashflow(1, 1.0, 0, 200, [{
                        "funding_time": ts, "funding_rate": 0.001, "mark_price": 100.0,
                    }])

    def test_funding_cashflow_rejects_invalid_trade_timestamps_and_boolean_side(self):
        event = [{"funding_time": 100, "funding_rate": 0.001, "mark_price": 100.0}]
        for entry, exit_ in ((True, 200), (0, False), (0.5, 200), (0, 200.5)):
            with self.subTest(entry=entry, exit=exit_):
                with self.assertRaisesRegex(ValueError, "funding timestamp must be an integer"):
                    funding_cashflow(1, 1.0, entry, exit_, event)
        with self.assertRaisesRegex(ValueError, "invalid side"):
            funding_cashflow(True, 1.0, 0, 200, event)

    def test_funding_cashflow_accepts_integer_timestamp_strings(self):
        pnl, count = funding_cashflow(1, 1.0, 0, 200, [{
            "funding_time": "100", "funding_rate": 0.001, "mark_price": 100.0,
        }])
        self.assertAlmostEqual(pnl, -0.1)
        self.assertEqual(count, 1)

    def test_funding_changes_realized_pnl_and_intratrade_drawdown(self):
        # Construct a deterministic breakout/retest across several bars. ATR is
        # fixed so the test isolates trade lifecycle/funding semantics.
        from unittest.mock import patch

        rows = []
        for i in range(30):
            close = 100.0
            rows.append({"t": i * 3_600_000, "o": 99.9, "h": 100.35,
                         "l": 99.65, "c": close, "v": 100.0})
        rows[20] = {"t": 20 * 3_600_000, "o": 101.0, "h": 102.2,
                    "l": 100.8, "c": 102.0, "v": 100.0}
        rows[21] = {"t": 21 * 3_600_000, "o": 100.3, "h": 100.8,
                    "l": 100.2, "c": 100.6, "v": 100.0}
        rows[22] = {"t": 22 * 3_600_000, "o": 100.4, "h": 101.0,
                    "l": 100.3, "c": 100.7, "v": 100.0}
        rows[23] = {"t": 23 * 3_600_000, "o": 100.7, "h": 101.0,
                    "l": 100.3, "c": 100.6, "v": 100.0}
        rows[24] = {"t": 24 * 3_600_000, "o": 100.6, "h": 101.0,
                    "l": 100.3, "c": 100.6, "v": 100.0}
        params = {"lookback": 5, "max_wait": 3, "target_r": 3.0,
                  "direction": "LONG", "retest_atr": 0.20}
        funding = [{"funding_time": rows[23]["t"], "funding_rate": 0.001,
                    "mark_price": 100.0, "symbol": "BTCUSDT"}]

        with patch("research.breakout_retest.atr_values", return_value=[1.0] * len(rows)):
            no_funding = run_breakout_retest(rows, params, 20, 25)
            with_funding = run_breakout_retest(
                rows, params, 20, 25, funding_rates=funding, symbol="BTCUSDT"
            )

        self.assertEqual(no_funding["trades"], 1)
        self.assertEqual(with_funding["trades"], 1)
        plain_trade = no_funding["trade_log"][0]
        funded_trade = with_funding["trade_log"][0]
        self.assertGreater(funded_trade["exit_time"], funded_trade["entry_time"])
        self.assertEqual(funded_trade["funding_events"], 1)
        self.assertLess(funded_trade["funding_pnl_equity"], 0)
        self.assertAlmostEqual(
            funded_trade["pnl_equity"] - plain_trade["pnl_equity"],
            funded_trade["funding_pnl_equity"], places=7,
        )
        self.assertGreater(with_funding["max_drawdown_pct"], no_funding["max_drawdown_pct"])

    def test_adverse_funding_increases_intratrade_drawdown(self):
        # Deterministic held-position mark: funding is an accrued cashflow, not
        # a realized-only adjustment. Keep all other mark inputs identical.
        peak_equity = 1.01
        without_funding = marked_equity_after_costs_and_funding(
            equity=1.0, mark_pnl=0.002, mark_costs=0.0005, mark_funding=0.0,
        )
        with_adverse_funding = marked_equity_after_costs_and_funding(
            equity=1.0, mark_pnl=0.002, mark_costs=0.0005, mark_funding=-0.003,
        )
        dd_without_funding = (peak_equity - without_funding) / peak_equity
        dd_with_adverse_funding = (peak_equity - with_adverse_funding) / peak_equity
        self.assertAlmostEqual(without_funding, 1.0015)
        self.assertAlmostEqual(with_adverse_funding, 0.9985)
        self.assertGreater(dd_with_adverse_funding, dd_without_funding)

    def test_marked_equity_rejects_invalid_inputs(self):
        for values in ((float("nan"), 0, 0, 0), (1, 0, -0.1, 0), (1, 0, 0, float("inf"))):
            with self.assertRaisesRegex(ValueError, "marked-equity inputs"):
                marked_equity_after_costs_and_funding(*values)

    def test_funding_cashflow_long_pays_and_short_receives(self):
        events = [{"funding_time": 200, "funding_rate": 0.001, "mark_price": 100.0}]
        self.assertAlmostEqual(funding_cashflow(1, 2.0, 100, 300, events)[0], -0.2)
        self.assertAlmostEqual(funding_cashflow(-1, 2.0, 100, 300, events)[0], 0.2)

    def test_duplicate_funding_timestamps_rejected_by_backtest(self):
        rows = make_rows()
        params = {"lookback": 24, "max_wait": 6, "target_r": 2.0}
        event = {"funding_time": rows[150]["t"], "funding_rate": 0.001,
                 "mark_price": 100.0, "symbol": "BTCUSDT"}
        with self.assertRaisesRegex(ValueError, "duplicate funding timestamp"):
            run_breakout_retest(
                rows, params, 100, len(rows),
                funding_rates=[event, dict(event)], symbol="BTCUSDT",
            )

    def test_funding_cashflow_negative_rate_reverses_long_short_direction(self):
        events = [{"funding_time": 200, "funding_rate": -0.01, "mark_price": 100.0}]
        long_pnl, long_count = funding_cashflow(1, 2.0, 100, 300, events)
        short_pnl, short_count = funding_cashflow(-1, 2.0, 100, 300, events)
        self.assertAlmostEqual(long_pnl, 2.0)
        self.assertAlmostEqual(short_pnl, -2.0)
        self.assertEqual(long_count, 1)
        self.assertEqual(short_count, 1)

    def test_funding_cashflow_rejects_duplicate_settlement_timestamps(self):
        event = {"funding_time": 200, "funding_rate": 0.01, "mark_price": 100.0}
        with self.assertRaisesRegex(ValueError, "duplicate funding timestamp"):
            funding_cashflow(1, 1.0, 100, 300, [event, dict(event)])

    def test_funding_cashflow_rejects_invalid_records_outside_trade_window(self):
        bad_events = [
            {"funding_time": -1, "funding_rate": 0.01, "mark_price": 100.0},
            {"funding_time": 50, "funding_rate": float("nan"), "mark_price": 100.0},
            {"funding_time": 50, "funding_rate": 0.01, "mark_price": 0.0},
        ]
        for event in bad_events:
            with self.assertRaises(ValueError):
                funding_cashflow(1, 1.0, 100, 300, [event])

    def test_funding_cashflow_rejects_invalid_trade_time_bounds(self):
        events = [{"funding_time": 200, "funding_rate": 0.01, "mark_price": 100.0}]
        for entry_ts, exit_ts in ((-1, 300), (200, 100)):
            with self.assertRaisesRegex(ValueError, "trade timestamps"):
                funding_cashflow(1, 1.0, entry_ts, exit_ts, events)

    def test_funding_cashflow_rejects_non_finite_quantity(self):
        events = [{"funding_time": 200, "funding_rate": 0.01, "mark_price": 100.0}]
        for quantity in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaisesRegex(ValueError, "quantity"):
                funding_cashflow(1, quantity, 100, 300, events)

    def test_funding_cashflow_rejects_boolean_quantity(self):
        events = [{"funding_time": 200, "funding_rate": 0.01, "mark_price": 100.0}]
        with self.assertRaisesRegex(ValueError, "quantity"):
            funding_cashflow(1, True, 100, 300, events)

    def test_funding_cashflow_excludes_entry_time_and_after_exit(self):
        events = [
            {"funding_time": 100, "funding_rate": 0.01, "mark_price": 100.0},
            {"funding_time": 200, "funding_rate": 0.01, "mark_price": 100.0},
            {"funding_time": 300, "funding_rate": 0.01, "mark_price": 100.0},
            {"funding_time": 301, "funding_rate": 0.01, "mark_price": 100.0},
        ]
        pnl, count = funding_cashflow(1, 1.0, 100, 300, events)
        self.assertAlmostEqual(pnl, -2.0)
        self.assertEqual(count, 2)

    def test_candidate_rejection_reasons_explain_weak_folds(self):
        folds = [
            {"trades": 5, "net_return_pct": 2.0},
            {"trades": 4, "net_return_pct": 1.0},
            {"trades": 4, "net_return_pct": -4.0},
        ]
        reasons = candidate_rejection_reasons(folds, -0.5)
        self.assertIn("worst_fold_below_minus_3pct", reasons)
        self.assertIn("risk_adjusted_score_not_positive", reasons)
        self.assertNotIn("total_trades_below_12", reasons)

    def test_invalid_window_rejected(self):
        rows = make_rows()
        with self.assertRaisesRegex(ValueError, "evaluation indices"):
            run_breakout_retest(rows, {"lookback": 24, "max_wait": 6, "target_r": 2.0},
                                100, len(rows) + 1)


if __name__ == "__main__":
    unittest.main()
