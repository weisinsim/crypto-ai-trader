import unittest

from research.breakout_retest import run_breakout_retest_backtest, signal_for_entry


def candle(i, o, h, l, c):
    return {"t": i * 3_600_000, "o": o, "h": h, "l": l, "c": c, "v": 100.0}


def breakout_fixture():
    rows = [candle(i, 100.0, 101.0, 99.0, 100.0) for i in range(10)]
    rows.append(candle(10, 100.0, 104.0, 99.5, 103.0))  # close above prior resistance
    rows.append(candle(11, 102.0, 103.0, 101.4, 102.5))  # retest and hold
    rows.append(candle(12, 102.5, 104.0, 102.0, 103.5))  # confirmation
    rows.append(candle(13, 103.5, 104.2, 103.0, 104.0))  # next-open entry candle
    return rows


class BreakoutRetestTests(unittest.TestCase):
    def test_long_signal_requires_confirmation_before_entry_bar(self):
        rows = breakout_fixture()
        self.assertEqual(signal_for_entry(rows, 12, lookback=5, atr_period=2), 0)
        self.assertEqual(signal_for_entry(rows, 13, lookback=5, atr_period=2), 1)

    def test_backtest_reports_directional_counts_and_costs(self):
        rows = breakout_fixture() * 4
        # Keep timestamps strictly increasing after repeating the price pattern.
        for i, row in enumerate(rows):
            row["t"] = i * 3_600_000
        result = run_breakout_retest_backtest(
            rows, lookback=5, max_retest_bars=4, atr_period=2,
            fee_rate=0.0005, slippage_rate=0.0002,
        )
        self.assertEqual(result["status"], "COMPLETED")
        self.assertIn("long_trades", result)
        self.assertIn("short_trades", result)
        self.assertGreaterEqual(result["max_drawdown_pct"], 0)
        self.assertLessEqual(result["max_drawdown_pct"], 100)

    def test_invalid_costs_rejected(self):
        with self.assertRaisesRegex(ValueError, "finite"):
            run_breakout_retest_backtest(breakout_fixture(), lookback=5, atr_period=2,
                                         fee_rate=float("nan"))


if __name__ == "__main__":
    unittest.main()
