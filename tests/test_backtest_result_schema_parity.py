import unittest

from research.backtest import run_ema_cross_backtest


class BacktestResultSchemaParityTests(unittest.TestCase):
    def test_completed_and_insufficient_results_share_top_level_schema(self):
        insufficient = run_ema_cross_backtest([])
        candles = [
            {"t": i * 3_600_000, "o": 100, "h": 101, "l": 99, "c": 100, "v": 10}
            for i in range(10)
        ]
        completed = run_ema_cross_backtest(
            candles, fast=2, slow=4, atr_period=2
        )

        self.assertEqual(insufficient["status"], "INSUFFICIENT_DATA")
        self.assertEqual(completed["status"], "COMPLETED")
        self.assertEqual(set(completed), set(insufficient))

    def test_completed_result_exposes_finite_equity_curve_and_final_value(self):
        candles = [
            {"t": i * 3_600_000, "o": 100, "h": 101, "l": 99, "c": 100, "v": 10}
            for i in range(10)
        ]
        result = run_ema_cross_backtest(
            candles, fast=2, slow=4, atr_period=2
        )

        self.assertEqual(result["equity_curve"][-1]["equity"], result["equity_final"])
        self.assertTrue(all(
            isinstance(point["equity"], (int, float))
            and point["equity"] == point["equity"]
            and abs(point["equity"]) != float("inf")
            for point in result["equity_curve"]
        ))


if __name__ == "__main__":
    unittest.main()
