import unittest

from research.backtest import run_ema_cross_backtest, validate_candles


def candles_from_closes(closes):
    rows = []
    for i, close in enumerate(closes):
        rows.append({"t": i * 3_600_000, "o": close, "h": close * 1.01,
                     "l": close * 0.99, "c": close, "v": 100})
    return rows


class CandleValidationTests(unittest.TestCase):
    def test_rejects_non_mapping_candle_rows(self):
        for row in (None, [], "not-a-row", 123):
            with self.subTest(row=row), self.assertRaisesRegex(ValueError, "must be a mapping"):
                validate_candles([row])

    def test_rejects_duplicate_timestamps(self):
        with self.assertRaises(ValueError):
            validate_candles([{"t": 1, "o": 1, "h": 2, "l": .5, "c": 1},
                               {"t": 1, "o": 1, "h": 2, "l": .5, "c": 1}])

    def test_interval_ms_must_be_positive_integer(self):
        rows = [{"t": 0, "o": 1, "h": 2, "l": .5, "c": 1}]
        for interval in (0, -1, True, 1.5, "3600000"):
            with self.subTest(interval=interval), self.assertRaisesRegex(ValueError, "interval_ms"):
                validate_candles(rows, interval_ms=interval)

    def test_rejects_gaps_when_interval_required(self):
        rows = [{"t": 0, "o": 1, "h": 2, "l": .5, "c": 1},
                {"t": 7_200_000, "o": 1, "h": 2, "l": .5, "c": 1}]
        with self.assertRaises(ValueError):
            validate_candles(rows, interval_ms=3_600_000)

    def test_rejects_invalid_high(self):
        with self.assertRaises(ValueError):
            validate_candles([{"t": 0, "o": 10, "h": 9, "l": 8, "c": 9}])

    def test_rejects_fractional_boolean_and_negative_timestamps(self):
        valid = {"o": 100, "h": 101, "l": 99, "c": 100}
        for timestamp in (1.5, True, -1, "1.5", "1e3"):
            with self.subTest(timestamp=timestamp), self.assertRaisesRegex(
                ValueError, "timestamp"
            ):
                validate_candles([{"t": timestamp, **valid}])

    def test_accepts_integer_timestamp_string_without_truncation(self):
        rows = validate_candles([{"t": "1000", "o": 100, "h": 101, "l": 99, "c": 100}])
        self.assertEqual(rows[0]["t"], "1000")

    def test_rejects_negative_or_non_finite_volume_metrics(self):
        valid = {"t": 0, "o": 100, "h": 101, "l": 99, "c": 100}
        for field, value in (("v", -1), ("q", float("nan")), ("v", float("inf"))):
            with self.subTest(field=field, value=value), self.assertRaisesRegex(
                ValueError, f"invalid {field}"
            ):
                validate_candles([{**valid, field: value}])

    def test_rejects_boolean_ohlcv_values(self):
        valid = {"t": 0, "o": 100, "h": 101, "l": 99, "c": 100, "v": 10}
        for field in ("o", "h", "l", "c", "v", "q"):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "invalid"):
                validate_candles([{**valid, field: True}])


class BacktestTests(unittest.TestCase):
    def test_equity_floor_failure_stops_ema_backtest_and_is_reported(self):
        rows = candles_from_closes([100 + ((i % 8) - 4) * 2 for i in range(240)])
        result = run_ema_cross_backtest(
            rows, fast=3, slow=9, atr_period=3, fee_rate=0.99,
            slippage_rate=0, risk_fraction=1.0,
        )
        self.assertTrue(result["equity_floor_hit"])
        self.assertAlmostEqual(result["equity_final"], 1e-6)
        self.assertLess(len(result["trade_log"]), len(rows))

    def test_insufficient_data_is_explicit(self):
        result = run_ema_cross_backtest(candles_from_closes([100, 101, 102]), fast=2, slow=4)
        self.assertEqual(result["status"], "INSUFFICIENT_DATA")

    def test_no_trade_returns_none_metrics_not_fake_zero_win_rate(self):
        result = run_ema_cross_backtest(candles_from_closes([100 + i * .01 for i in range(80)]),
                                        fast=5, slow=10, atr_period=5)
        self.assertIn(result["status"], ("COMPLETED", "INSUFFICIENT_DATA"))
        if result["trades"] == 0:
            self.assertIsNone(result["win_rate_pct"])

    def test_invalid_parameters_rejected(self):
        rows = candles_from_closes([100 + i for i in range(30)])
        with self.assertRaises(ValueError):
            run_ema_cross_backtest(rows, fast=10, slow=5)

    def test_rejects_boolean_or_non_integer_period_parameters(self):
        rows = candles_from_closes([100 + ((i % 7) - 3) for i in range(100)])
        for name, value in (("fast", True), ("slow", 10.5),
                            ("atr_period", "14"), ("trend_period", False)):
            with self.subTest(name=name, value=value), self.assertRaisesRegex(ValueError, name):
                run_ema_cross_backtest(rows, **{name: value})

    def test_rejects_boolean_or_non_numeric_cost_parameters(self):
        rows = candles_from_closes([100 + ((i % 7) - 3) for i in range(100)])
        for name, value in (("fee_rate", True), ("slippage_rate", "0.001"),
                            ("stop_atr", None), ("risk_fraction", False)):
            with self.subTest(name=name, value=value), self.assertRaisesRegex(ValueError, name):
                run_ema_cross_backtest(rows, **{name: value})

    def test_trade_start_timestamp_must_be_non_negative_integer(self):
        rows = candles_from_closes([100 + ((i % 7) - 3) for i in range(100)])
        for value in (True, -1, 1.5, "3600000"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "trade_start_ts"):
                run_ema_cross_backtest(rows, trade_start_ts=value)

    def test_trend_filter_period_must_be_zero_or_at_least_two(self):
        rows = candles_from_closes([100 + ((i % 9) - 4) for i in range(120)])
        with self.assertRaisesRegex(ValueError, "trend_period"):
            run_ema_cross_backtest(rows, fast=3, slow=9, atr_period=3, trend_period=1)

    def test_trend_filter_is_optional_and_runs_with_long_period(self):
        rows = candles_from_closes([100 + ((i % 9) - 4) for i in range(180)])
        result = run_ema_cross_backtest(
            rows, fast=3, slow=9, atr_period=3, trend_period=50,
            fee_rate=0, slippage_rate=0,
        )
        self.assertEqual(result["status"], "COMPLETED")
        self.assertGreaterEqual(result["trades"], 0)

    def test_non_finite_cost_and_funding_parameters_rejected(self):
        rows = candles_from_closes([100 + (i % 5) for i in range(80)])
        for name, value in (("fee_rate", float("nan")),
                            ("slippage_rate", float("inf")),
                            ("funding_rate_per_bar", float("-inf"))):
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, "finite"):
                    run_ema_cross_backtest(rows, fast=3, slow=7, atr_period=3,
                                           **{name: value})

    def test_fee_and_slippage_rates_must_be_valid_fractions(self):
        rows = candles_from_closes([100 + (i % 5) for i in range(80)])
        for name, value in (("fee_rate", 1.0), ("fee_rate", -0.01),
                            ("slippage_rate", 1.0), ("slippage_rate", -0.01)):
            with self.subTest(name=name, value=value), self.assertRaisesRegex(ValueError, name):
                run_ema_cross_backtest(rows, fast=3, slow=7, atr_period=3,
                                       **{name: value})

    def test_historical_funding_is_applied_at_settlement_timestamp(self):
        rows = candles_from_closes([100 + ((i % 8) - 4) * 2 for i in range(160)])
        base = run_ema_cross_backtest(rows, fast=3, slow=9, atr_period=3,
                                      fee_rate=0, slippage_rate=0)
        held_trade = next((t for t in base["trade_log"]
                           if t["exit_time"] > t["entry_time"]), None)
        self.assertIsNotNone(held_trade)
        event_ts = held_trade["exit_time"]
        with_cost = run_ema_cross_backtest(
            rows, fast=3, slow=9, atr_period=3, fee_rate=0, slippage_rate=0,
            funding_rates=[{"funding_time": event_ts, "funding_rate": 0.01,
                            "mark_price": 100.0}],
        )
        target_trade = next(t for t in with_cost["trade_log"]
                            if t["entry_time"] == held_trade["entry_time"])
        self.assertNotEqual(target_trade["pnl_equity"], held_trade["pnl_equity"])
        self.assertEqual(target_trade["funding_settlement_count"], 1)
        self.assertLess(target_trade["funding_cashflow"], 0.0)
        self.assertIsInstance(target_trade["funding_boundary_ambiguous"], bool)

    def test_funding_event_timestamps_reject_boolean_fractional_and_negative_values(self):
        rows = candles_from_closes([100 + ((i % 8) - 4) * 2 for i in range(100)])
        for timestamp in (True, 1.5, -1, "1.5", "1e3"):
            event = {"funding_time": timestamp, "funding_rate": 0.001, "mark_price": 100.0}
            with self.subTest(timestamp=timestamp), self.assertRaisesRegex(ValueError, "timestamp"):
                run_ema_cross_backtest(rows, fast=3, slow=9, atr_period=3, funding_rates=[event])

    def test_funding_event_schema_errors_are_clear(self):
        rows = candles_from_closes([100 + ((i % 8) - 4) * 2 for i in range(100)])
        bad_events = (
            [1, 2, 3],
            {"funding_time": rows[50]["t"], "funding_rate": 0.001},
            {"funding_time": rows[50]["t"], "funding_rate": True, "mark_price": 100.0},
            {"funding_time": rows[50]["t"], "funding_rate": 0.001, "mark_price": False},
        )
        for event in bad_events:
            with self.subTest(event=event), self.assertRaises(ValueError):
                run_ema_cross_backtest(rows, fast=3, slow=9, atr_period=3, funding_rates=[event])

    def test_funding_events_with_empty_candles_fail_clearly(self):
        event = {"funding_time": 0, "funding_rate": 0.001, "mark_price": 100.0}
        with self.assertRaisesRegex(ValueError, "without candle data"):
            run_ema_cross_backtest([], fast=3, slow=9, funding_rates=[event])

    def test_positive_funding_rate_costs_longs_and_credits_shorts(self):
        rows = candles_from_closes([100 + ((i % 8) - 4) * 2 for i in range(240)])
        kwargs = dict(fast=3, slow=9, atr_period=3, fee_rate=0, slippage_rate=0)
        baseline = run_ema_cross_backtest(rows, **kwargs)
        for side in ("LONG", "SHORT"):
            with self.subTest(side=side):
                trade = next((t for t in baseline["trade_log"]
                              if t["side"] == side and t["exit_time"] > t["entry_time"]), None)
                self.assertIsNotNone(trade, f"fixture must produce a held {side} trade")
                event = {"funding_time": trade["exit_time"], "funding_rate": 0.01,
                         "mark_price": 100.0}
                funded = run_ema_cross_backtest(rows, **kwargs, funding_rates=[event])
                funded_trade = next(t for t in funded["trade_log"]
                                    if t["entry_time"] == trade["entry_time"] and t["side"] == side)
                self.assertEqual(funded_trade["funding_settlement_count"], 1)
                if side == "LONG":
                    self.assertLess(funded_trade["pnl_equity"], trade["pnl_equity"])
                    self.assertLess(funded_trade["funding_cashflow"], 0.0)
                else:
                    self.assertGreater(funded_trade["pnl_equity"], trade["pnl_equity"])
                    self.assertGreater(funded_trade["funding_cashflow"], 0.0)

    def test_duplicate_historical_funding_timestamps_rejected(self):
        rows = candles_from_closes([100 + ((i % 8) - 4) * 2 for i in range(100)])
        event = {"funding_time": rows[50]["t"], "funding_rate": 0.001, "mark_price": 100.0}
        with self.assertRaisesRegex(ValueError, "duplicate funding timestamp"):
            run_ema_cross_backtest(rows, fast=3, slow=9, atr_period=3,
                                   funding_rates=[event, event])

    def test_funding_timestamp_with_small_millisecond_offset_is_rejected(self):
        rows = candles_from_closes([100 + ((i % 8) - 4) * 2 for i in range(100)])
        event_ts = rows[50]["t"] + 13
        with self.assertRaisesRegex(ValueError, "does not exactly match a candle open"):
            run_ema_cross_backtest(
                rows, fast=3, slow=9, atr_period=3, fee_rate=0, slippage_rate=0,
                funding_rates=[{"funding_time": event_ts, "funding_rate": 0.001,
                                "mark_price": 100.0}],
            )
    def test_funding_timestamp_far_from_candle_open_is_rejected(self):
        rows = candles_from_closes([100 + ((i % 8) - 4) * 2 for i in range(100)])
        event_ts = rows[50]["t"] + 1001
        with self.assertRaisesRegex(ValueError, "does not exactly match a candle open"):
            run_ema_cross_backtest(
                rows, fast=3, slow=9, atr_period=3,
                funding_rates=[{"funding_time": event_ts, "funding_rate": 0.001,
                                "mark_price": 100.0}],
            )

    def test_risk_fraction_must_be_between_zero_and_one(self):
        rows = candles_from_closes([100 + (i % 5) for i in range(80)])
        for risk in (0, -0.1, 1.1):
            with self.subTest(risk=risk):
                with self.assertRaises(ValueError):
                    run_ema_cross_backtest(rows, fast=3, slow=7, atr_period=3,
                                           risk_fraction=risk)

    def test_trade_start_timestamp_blocks_warmup_entries(self):
        rows = candles_from_closes([100 + ((i % 8) - 4) * 2 for i in range(160)])
        boundary = rows[100]["t"]
        result = run_ema_cross_backtest(
            rows, fast=3, slow=9, atr_period=3, fee_rate=0, slippage_rate=0,
            trade_start_ts=boundary,
        )
        self.assertTrue(all(t["entry_time"] >= boundary for t in result["trade_log"]))

    def test_trade_start_timestamp_preserves_indicator_warmup(self):
        rows = candles_from_closes([100 + ((i % 8) - 4) * 2 for i in range(160)])
        boundary = rows[100]["t"]
        isolated = run_ema_cross_backtest(
            rows, fast=3, slow=9, atr_period=3, fee_rate=0, slippage_rate=0,
            trade_start_ts=boundary,
        )
        self.assertGreater(isolated["trades"], 0)
        self.assertTrue(all(t["entry_time"] >= boundary for t in isolated["trade_log"]))

    def test_intrabar_adverse_extreme_increases_drawdown_without_stop_fill(self):
        rows = []
        closes = [100 + ((i % 8) - 4) * 0.15 for i in range(180)]
        for i, close in enumerate(closes):
            # Keep adverse wicks material but within the deliberately wide stop.
            rows.append({"t": i * 3_600_000, "o": close, "h": close + 0.12,
                         "l": close - 0.12, "c": close, "v": 100})
        result = run_ema_cross_backtest(
            rows, fast=3, slow=9, atr_period=3, stop_atr=100, target_atr=100,
            fee_rate=0, slippage_rate=0,
        )
        self.assertEqual(result["status"], "COMPLETED")
        self.assertGreater(result["trades"], 0)
        # The public curve remains close-marked. Intrabar adverse marking must
        # make reported DD at least as large as the close-only curve DD.
        peak = 1.0
        close_only_dd = 0.0
        for point in result["equity_curve"]:
            value = point["equity"]
            peak = max(peak, value)
            if peak > 0:
                close_only_dd = max(close_only_dd, (peak - value) / peak * 100)
        self.assertGreater(result["max_drawdown_pct"], round(close_only_dd, 4))

    def test_entry_bar_adverse_wick_is_counted_before_same_bar_exit(self):
        closes = [100 + ((i % 8) - 4) * 0.15 for i in range(180)]
        rows = [{"t": i * 3_600_000, "o": close, "h": close + 0.12,
                 "l": close - 0.12, "c": close, "v": 100}
                for i, close in enumerate(closes)]
        kwargs = dict(fast=3, slow=9, atr_period=3, stop_atr=20, target_atr=100,
                      fee_rate=0, slippage_rate=0)
        baseline = run_ema_cross_backtest(rows, **kwargs)
        self.assertGreater(baseline["trades"], 0)
        first_trade = baseline["trade_log"][0]
        entry_index = next(i for i, row in enumerate(rows)
                           if row["t"] == first_trade["entry_time"])

        stressed_rows = [dict(row) for row in rows]
        if first_trade["side"] == "LONG":
            stressed_rows[entry_index]["l"] = stressed_rows[entry_index]["c"] - 4.0
        else:
            stressed_rows[entry_index]["h"] = stressed_rows[entry_index]["c"] + 4.0

        stressed = run_ema_cross_backtest(stressed_rows, **kwargs)
        self.assertEqual(stressed["status"], "COMPLETED")
        # The altered wick is on the entry candle and stays inside the wide
        # stop/target. Its adverse mark must increase reported drawdown even
        # though the close-only equity path is unchanged.
        self.assertGreater(stressed["max_drawdown_pct"], baseline["max_drawdown_pct"])

    def test_entry_bar_adverse_wick_is_counted_for_both_trade_directions(self):
        closes = [100 + ((i % 8) - 4) * 0.15 for i in range(240)]
        rows = [{"t": i * 3_600_000, "o": close, "h": close + 0.12,
                 "l": close - 0.12, "c": close, "v": 100}
                for i, close in enumerate(closes)]
        # A tighter stop lets this deliberately oscillating series flatten
        # positions between EMA crosses, so both long and short entries occur.
        kwargs = dict(fast=3, slow=9, atr_period=3, stop_atr=0.5, target_atr=100,
                      fee_rate=0, slippage_rate=0)
        baseline = run_ema_cross_backtest(rows, **kwargs)
        self.assertEqual(baseline["status"], "COMPLETED")
        sides = {trade["side"] for trade in baseline["trade_log"]}
        self.assertIn("LONG", sides)
        self.assertIn("SHORT", sides)
        for side, adverse_key, delta in (("LONG", "l", -100.0), ("SHORT", "h", 100.0)):
            with self.subTest(side=side):
                trade = next(t for t in baseline["trade_log"] if t["side"] == side)
                index = next(i for i, row in enumerate(rows) if row["t"] == trade["entry_time"])
                stressed_rows = [dict(row) for row in rows]
                stressed_rows[index][adverse_key] += delta
                stressed = run_ema_cross_backtest(stressed_rows, **kwargs)
                self.assertEqual(stressed["status"], "COMPLETED")
                self.assertGreater(stressed["max_drawdown_pct"], baseline["max_drawdown_pct"])

    def test_same_bar_stop_and_target_prefers_stop(self):
        rows = candles_from_closes([100 + (i % 3) for i in range(80)])
        result = run_ema_cross_backtest(rows, fast=3, slow=7, atr_period=3, stop_atr=.1,
                                        target_atr=.1, fee_rate=0, slippage_rate=0)
        # Reconstruct the exit candle; if both barriers were touched, STOP must win.
        by_time = {row["t"]: row for row in rows}
        for trade in result["trade_log"]:
            bar = by_time.get(trade["exit_time"])
            if bar is None:
                continue
            if trade["side"] == "LONG":
                both_hit = bar["l"] <= trade["stop"] and bar["h"] >= trade["target"]
            else:
                both_hit = bar["h"] >= trade["stop"] and bar["l"] <= trade["target"]
            if both_hit:
                self.assertEqual(trade["reason"], "STOP")


    def test_funding_settlement_changes_trade_pnl_and_reported_drawdown(self):
        rows = candles_from_closes([100 + ((i % 8) - 4) * 2 for i in range(240)])
        kwargs = dict(fast=3, slow=9, atr_period=3, fee_rate=0, slippage_rate=0)
        baseline = run_ema_cross_backtest(rows, **kwargs)
        trade = next(t for t in baseline["trade_log"]
                     if t["side"] == "LONG" and t["exit_time"] > t["entry_time"])
        event = {"funding_time": trade["exit_time"], "funding_rate": 0.05,
                 "mark_price": 100.0}
        funded = run_ema_cross_backtest(rows, **kwargs, funding_rates=[event])
        funded_trade = next(t for t in funded["trade_log"]
                            if t["entry_time"] == trade["entry_time"] and t["side"] == "LONG")
        self.assertLess(funded_trade["pnl_equity"], trade["pnl_equity"])
        self.assertGreater(funded["max_drawdown_pct"], baseline["max_drawdown_pct"])


    def test_funding_boundary_is_flagged_for_gap_stop_at_settlement_open(self):
        rows = candles_from_closes([100 + ((i % 8) - 4) * 2 for i in range(240)])
        kwargs = dict(fast=3, slow=9, atr_period=3, fee_rate=0, slippage_rate=0)
        baseline = run_ema_cross_backtest(rows, **kwargs)
        trade = next(t for t in baseline["trade_log"]
                     if t["bars_held"] > 0 and t["reason"] == "STOP")
        index = next(i for i, row in enumerate(rows) if row["t"] == trade["exit_time"])
        stressed_rows = [dict(row) for row in rows]
        bar = stressed_rows[index]
        if trade["side"] == "LONG":
            bar["o"] = trade["stop"] - 1.0
            bar["l"] = min(bar["l"], bar["o"])
            bar["h"] = max(bar["h"], bar["o"], bar["c"])
        else:
            bar["o"] = trade["stop"] + 1.0
            bar["h"] = max(bar["h"], bar["o"])
            bar["l"] = min(bar["l"], bar["o"], bar["c"])
        event = {"funding_time": bar["t"], "funding_rate": 0.001, "mark_price": 100.0}
        result = run_ema_cross_backtest(stressed_rows, **kwargs, funding_rates=[event])
        affected = next(t for t in result["trade_log"]
                        if t["entry_time"] == trade["entry_time"] and t["exit_time"] == trade["exit_time"])
        self.assertEqual(affected["reason"], "STOP")
        self.assertEqual(affected["funding_settlement_count"], 1)
        self.assertTrue(affected["funding_boundary_ambiguous"])


if __name__ == "__main__":
    unittest.main()
