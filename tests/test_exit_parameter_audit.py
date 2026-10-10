import unittest

from validation.exit_parameter_audit import evaluate, summarize, parse_ts


def candle(ts, open_, high, low, close):
    return {"ts": ts, "open": open_, "high": high, "low": low, "close": close}


class ExitAuditTests(unittest.TestCase):
    def test_epoch_milliseconds_are_normalized_to_seconds(self):
        self.assertEqual(parse_ts("1791600000000"), 1791600000)

    def test_target_hit_on_future_candle(self):
        candles = [
            candle(1, 100, 101, 99, 100),
            candle(2, 100, 106, 99, 104),
        ]
        signals = [{"ts": 1, "side": "LONG", "entry": 100, "atr": 1}]
        trades = evaluate(candles, signals, target_r=2.5, stop_atr=2,
                          cost_bps=0, max_hold=5)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["exit_reason"], "TP")
        self.assertAlmostEqual(trades[0]["net_R"], 2.5)

    def test_stop_first_when_stop_and_target_same_candle(self):
        candles = [
            candle(1, 100, 101, 99, 100),
            candle(2, 100, 106, 95, 102),
        ]
        signals = [{"ts": 1, "side": "LONG", "entry": 100, "atr": 1}]
        trades = evaluate(candles, signals, target_r=2.5, stop_atr=2,
                          cost_bps=0, max_hold=5)
        self.assertEqual(trades[0]["exit_reason"], "SL")
        self.assertEqual(trades[0]["net_R"], -1.0)

    def test_short_direction_stop_and_target(self):
        candles = [
            candle(1, 100, 101, 99, 100),
            candle(2, 100, 101, 94, 96),
        ]
        signals = [{"ts": 1, "side": "SHORT", "entry": 100, "atr": 1}]
        trades = evaluate(candles, signals, target_r=2.5, stop_atr=2,
                          cost_bps=0, max_hold=5)
        self.assertEqual(trades[0]["exit_reason"], "TP")
        self.assertAlmostEqual(trades[0]["net_R"], 2.5)

    def test_round_trip_cost_is_subtracted_in_r(self):
        candles = [
            candle(1, 100, 101, 99, 100),
            candle(2, 100, 106, 99, 104),
        ]
        signals = [{"ts": 1, "side": "LONG", "entry": 100, "atr": 1}]
        trades = evaluate(candles, signals, target_r=2.5, stop_atr=2,
                          cost_bps=10, max_hold=5)
        self.assertAlmostEqual(trades[0]["cost_R"], 0.05)
        self.assertAlmostEqual(trades[0]["net_R"], 2.45)

    def test_unmatched_signal_is_not_counted(self):
        candles = [candle(1, 100, 101, 99, 100), candle(2, 100, 101, 99, 100)]
        signals = [{"ts": 9, "side": "LONG", "entry": 100, "atr": 1}]
        self.assertEqual(evaluate(candles, signals, 2.5, 2, 0, 5), [])

    def test_signal_specific_stop_overrides_fallback_atr(self):
        candles = [
            candle(1, 100, 101, 99, 100),
            candle(2, 100, 107, 99, 106),
        ]
        signals = [{"ts": 1, "side": "LONG", "entry": 100, "atr": 1, "stop": 98}]
        trades = evaluate(candles, signals, target_r=2.5, stop_atr=9,
                          cost_bps=0, max_hold=5)
        self.assertEqual(trades[0]["exit_reason"], "TP")
        self.assertAlmostEqual(trades[0]["net_R"], 2.5)

    def test_invalid_signal_stop_is_rejected(self):
        candles = [candle(1, 100, 101, 99, 100), candle(2, 100, 101, 99, 100)]
        signals = [{"ts": 1, "side": "LONG", "entry": 100, "atr": 1, "stop": 101}]
        with self.assertRaises(ValueError):
            evaluate(candles, signals, 2.5, 2, 0, 5)

    def test_development_trade_cannot_exit_using_holdout_candles(self):
        candles = [
            candle(1, 100, 101, 99, 100),
            candle(2, 100, 101, 99, 100),
            candle(3, 100, 110, 99, 109),
            candle(4, 100, 110, 99, 109),
        ]
        # The signal at ts=1 would hit 2.5R only on a later candle; if the
        # development slice ends at ts=2, its result must not use ts=3 or ts=4.
        dev_candles = candles[:2]
        signals = [{"ts": 1, "side": "LONG", "entry": 100, "atr": 1}]
        trades = evaluate(dev_candles, signals, target_r=2.5, stop_atr=2,
                          cost_bps=0, max_hold=72)
        self.assertEqual(trades[0]["exit_reason"], "TIME")
        self.assertEqual(trades[0]["gross_R"], 0.0)

    def test_summary_counts_trades(self):
        stats = summarize([
            {"net_R": 2.0, "exit_reason": "TP"},
            {"net_R": -1.0, "exit_reason": "SL"},
        ])
        self.assertEqual(stats["trades"], 2)
        self.assertEqual(stats["win_rate_pct"], 50.0)
        self.assertEqual(stats["profit_factor"], 2.0)
        self.assertEqual(stats["net_R"], 1.0)


if __name__ == "__main__":
    unittest.main()
