import unittest

from research.breakout_retest import atr_values, run_breakout_retest


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
    def test_atr_warmup_and_positive_values(self):
        rows = make_rows()
        atr = atr_values(rows, period=14)
        self.assertIsNone(atr[12])
        self.assertIsNotNone(atr[13])
        self.assertGreater(atr[13], 0)

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

    def test_invalid_costs_rejected(self):
        rows = make_rows()
        params = {"lookback": 24, "max_wait": 6, "target_r": 2.0}
        with self.assertRaisesRegex(ValueError, "fees and slippage"):
            run_breakout_retest(rows, params, 100, len(rows), fee_rate=float("nan"))

    def test_invalid_window_rejected(self):
        rows = make_rows()
        with self.assertRaisesRegex(ValueError, "evaluation indices"):
            run_breakout_retest(rows, {"lookback": 24, "max_wait": 6, "target_r": 2.0},
                                100, len(rows) + 1)


if __name__ == "__main__":
    unittest.main()
