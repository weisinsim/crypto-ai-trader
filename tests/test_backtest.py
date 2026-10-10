import unittest

from research.backtest import run_ema_cross_backtest, validate_candles


def candles_from_closes(closes):
    rows = []
    for i, close in enumerate(closes):
        rows.append({"t": i * 3_600_000, "o": close, "h": close * 1.01,
                     "l": close * 0.99, "c": close, "v": 100})
    return rows


class CandleValidationTests(unittest.TestCase):
    def test_rejects_duplicate_timestamps(self):
        with self.assertRaises(ValueError):
            validate_candles([{"t": 1, "o": 1, "h": 2, "l": .5, "c": 1},
                               {"t": 1, "o": 1, "h": 2, "l": .5, "c": 1}])

    def test_rejects_gaps_when_interval_required(self):
        rows = [{"t": 0, "o": 1, "h": 2, "l": .5, "c": 1},
                {"t": 7_200_000, "o": 1, "h": 2, "l": .5, "c": 1}]
        with self.assertRaises(ValueError):
            validate_candles(rows, interval_ms=3_600_000)

    def test_rejects_invalid_high(self):
        with self.assertRaises(ValueError):
            validate_candles([{"t": 0, "o": 10, "h": 9, "l": 8, "c": 9}])


class BacktestTests(unittest.TestCase):
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

    def test_non_finite_cost_and_funding_parameters_rejected(self):
        rows = candles_from_closes([100 + (i % 5) for i in range(80)])
        for name, value in (("fee_rate", float("nan")),
                            ("slippage_rate", float("inf")),
                            ("funding_rate_per_bar", float("-inf"))):
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, "finite"):
                    run_ema_cross_backtest(rows, fast=3, slow=7, atr_period=3,
                                           **{name: value})

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


if __name__ == "__main__":
    unittest.main()
