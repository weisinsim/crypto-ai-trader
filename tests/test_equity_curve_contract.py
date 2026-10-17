import math
import unittest

from research.backtest import run_ema_cross_backtest


def candles_from_closes(closes):
    return [
        {
            "t": i * 3_600_000,
            "o": close,
            "h": close * 1.01,
            "l": close * 0.99,
            "c": close,
            "v": 100,
        }
        for i, close in enumerate(closes)
    ]


class EquityCurveContractTests(unittest.TestCase):
    def assert_curve_contract(self, result):
        self.assertEqual(result["status"], "COMPLETED")
        curve = result["equity_curve"]
        self.assertTrue(curve)
        timestamps = [point["t"] for point in curve]
        self.assertEqual(timestamps, sorted(set(timestamps)))
        for point in curve:
            self.assertTrue(math.isfinite(point["equity"]))
        self.assertAlmostEqual(curve[-1]["equity"], result["equity_final"], places=8)

    def test_completed_backtest_curve_ends_at_reported_final_equity(self):
        rows = candles_from_closes([100 + ((i % 8) - 4) * 2 for i in range(240)])
        result = run_ema_cross_backtest(
            rows, fast=3, slow=9, atr_period=3, fee_rate=0.001,
            slippage_rate=0.0005, risk_fraction=0.01,
        )
        self.assert_curve_contract(result)

    def test_equity_floor_backtest_curve_ends_at_clamped_final_equity(self):
        rows = candles_from_closes([100 + ((i % 8) - 4) * 2 for i in range(240)])
        result = run_ema_cross_backtest(
            rows, fast=3, slow=9, atr_period=3, fee_rate=0.99,
            slippage_rate=0, risk_fraction=1.0,
        )
        self.assertTrue(result["equity_floor_hit"])
        self.assert_curve_contract(result)


if __name__ == "__main__":
    unittest.main()
