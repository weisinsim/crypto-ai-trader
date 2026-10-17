import unittest

from research.backtest import run_ema_cross_backtest


class InsufficientDataSchemaTests(unittest.TestCase):
    def test_empty_input_returns_complete_result_schema(self):
        result = run_ema_cross_backtest([])
        self.assertEqual(result["status"], "INSUFFICIENT_DATA")
        self.assertEqual(result["equity_final"], 1.0)
        self.assertEqual(result["equity_curve"], [])
        self.assertEqual(result["trade_log"], [])
        self.assertEqual(result["funding_boundary_ambiguous_trades"], 0)

    def test_short_input_returns_initial_equity_curve(self):
        rows = [
            {"t": i * 3_600_000, "o": 100, "h": 101, "l": 99, "c": 100, "v": 10}
            for i in range(3)
        ]
        result = run_ema_cross_backtest(rows, fast=2, slow=4, atr_period=2)
        self.assertEqual(result["status"], "INSUFFICIENT_DATA")
        self.assertEqual(result["equity_final"], 1.0)
        self.assertEqual(
            result["equity_curve"],
            [{"t": row["t"], "equity": 1.0} for row in rows],
        )
        self.assertEqual(result["bars"], len(rows))
        self.assertEqual(result["trades"], 0)


if __name__ == "__main__":
    unittest.main()
