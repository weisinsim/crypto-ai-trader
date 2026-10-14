import unittest

from validation.daily_loss_guard import DailyLossGuard
from validation.portfolio_order_gate import evaluate_order_gate_with_paper_portfolio


class PortfolioOrderGateTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = {
            "exchange_healthy": True,
            "kill_switch_active": False,
            "candle_closed": True,
            "model_status": "PRODUCTION",
            "signal": "LONG",
            "data_age_sec": 1,
            "analysis_age_sec": 1,
            "spread_bps": 2,
            "risk_fraction": 0.01,
            "daily_loss_fraction": 0.0,
            "open_positions": 1,
            "requested_notional": 100,
        }
        self.limits = {
            "max_data_age_sec": 10,
            "max_analysis_age_sec": 10,
            "max_spread_bps": 10,
            "max_risk_fraction": 0.02,
            "max_daily_loss_fraction": 0.05,
            "max_open_positions": 3,
            "max_notional": 1000,
        }
        self.guard = DailyLossGuard(100)
        self.orders = {
            "entry-1": {
                "symbol": "BTCUSDT", "side": "LONG", "status": "CANCELLED",
                "quantity": 2.0, "filled_quantity": 0.5,
            }
        }

    def evaluate(self):
        return evaluate_order_gate_with_paper_portfolio(
            self.snapshot, self.limits, self.guard, "2026-10-14", self.orders
        )

    def test_matching_partial_cancel_exposure_allows_base_gate(self):
        result = self.evaluate()
        self.assertTrue(result["approved"])
        self.assertEqual(result["portfolio_open_positions"], 1)
        self.assertEqual(result["portfolio_symbols"]["BTCUSDT"]["net_quantity"], 0.5)

    def test_stale_snapshot_position_count_fails_closed(self):
        self.snapshot["open_positions"] = 0
        result = self.evaluate()
        self.assertFalse(result["approved"])
        self.assertIn("PAPER_PORTFOLIO_POSITION_COUNT_MISMATCH", result["reasons"])

    def test_malformed_portfolio_fails_closed(self):
        self.orders["broken"] = {"symbol": "ETHUSDT"}
        result = self.evaluate()
        self.assertFalse(result["approved"])
        self.assertEqual(result["reasons"], ["PAPER_PORTFOLIO_INVALID"])

    def test_opposing_legs_still_count_as_one_active_symbol(self):
        self.orders["short-1"] = {
            "symbol": "BTCUSDT", "side": "SHORT", "status": "FILLED",
            "quantity": 0.5, "filled_quantity": 0.5,
        }
        result = self.evaluate()
        self.assertTrue(result["approved"])
        self.assertEqual(result["portfolio_open_positions"], 1)
        self.assertEqual(result["portfolio_symbols"]["BTCUSDT"]["net_quantity"], 0.0)
        self.assertEqual(result["portfolio_symbols"]["BTCUSDT"]["open_quantity"], 1.0)


if __name__ == "__main__":
    unittest.main()
