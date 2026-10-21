"""Tests for paper replay metrics; no market data or orders are used."""
import unittest

from execution.paper_replay import summarize_closed_trades


def closed(gross, fees=0.0, funding=0.0):
    return {
        "status": "CLOSED",
        "realized_gross_pnl": gross,
        "fees_paid": fees,
        "funding_paid": funding,
    }


class PaperReplayMetricsTests(unittest.TestCase):
    def test_metrics_include_fees_funding_and_equity_drawdown(self):
        result = summarize_closed_trades(
            [closed(20, 1, 1), closed(-10, 1, 0), closed(5, 0.5, 0)],
            starting_equity=100,
        )
        self.assertEqual(result["trade_count"], 3)
        self.assertEqual(result["win_count"], 2)
        self.assertEqual(result["loss_count"], 1)
        self.assertAlmostEqual(result["win_rate"], 2 / 3)
        self.assertAlmostEqual(result["net_pnl"], 11.5)
        self.assertAlmostEqual(result["fees_paid"], 2.5)
        self.assertAlmostEqual(result["funding_paid"], 1)
        self.assertAlmostEqual(result["ending_equity"], 111.5)
        self.assertAlmostEqual(result["max_drawdown_pct"], 11 / 118 * 100)
        self.assertTrue(result["research_only"])

    def test_empty_input_returns_zero_metrics(self):
        result = summarize_closed_trades([])
        self.assertEqual(result["trade_count"], 0)
        self.assertEqual(result["net_pnl"], 0)
        self.assertEqual(result["win_rate"], 0)

    def test_rejects_open_trade(self):
        with self.assertRaisesRegex(ValueError, "all_trades_must_be_closed"):
            summarize_closed_trades([{"status": "OPEN"}])

    def test_rejects_invalid_equity(self):
        with self.assertRaisesRegex(ValueError, "invalid_starting_equity"):
            summarize_closed_trades([], starting_equity=0)


if __name__ == "__main__":
    unittest.main()
