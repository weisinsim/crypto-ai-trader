import unittest

from research.backtest import run_ema_cross_backtest


class TrendPeriodValidationTests(unittest.TestCase):
    def test_invalid_trend_period_is_rejected_even_when_data_is_insufficient(self):
        with self.assertRaisesRegex(ValueError, "trend_period must be 0"):
            run_ema_cross_backtest([], trend_period=1)

    def test_invalid_trend_period_is_rejected_with_sufficient_data(self):
        candles = [
            {"t": i * 3_600_000, "o": 100, "h": 101, "l": 99, "c": 100, "v": 10}
            for i in range(60)
        ]
        with self.assertRaisesRegex(ValueError, "trend_period must be 0"):
            run_ema_cross_backtest(candles, fast=2, slow=4, atr_period=2, trend_period=1)


if __name__ == "__main__":
    unittest.main()
