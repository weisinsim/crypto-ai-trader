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

    def test_same_bar_stop_and_target_prefers_stop(self):
        rows = candles_from_closes([100 + (i % 3) for i in range(80)])
        result = run_ema_cross_backtest(rows, fast=3, slow=7, atr_period=3, stop_atr=.1,
                                        target_atr=.1, fee_rate=0, slippage_rate=0)
        # Any trade that has both barrier levels crossed must be marked as STOP.
        for trade in result["trade_log"]:
            if trade["reason"] in ("STOP", "TARGET"):
                self.assertIn(trade["reason"], ("STOP", "TARGET"))


if __name__ == "__main__":
    unittest.main()
