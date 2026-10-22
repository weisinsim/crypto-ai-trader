import unittest

from validation.portfolio_risk_audit import audit_portfolio_risk


class PortfolioRiskAuditTests(unittest.TestCase):
    def test_overlapping_symbols_trigger_portfolio_limit(self):
        trades = [
            {"trade_id": "a", "symbol": "BTCUSDT", "timestamp": 10, "exit_timestamp": 30, "risk_pct": 1.0},
            {"trade_id": "b", "symbol": "ETHUSDT", "timestamp": 20, "exit_timestamp": 40, "risk_pct": 1.2},
        ]
        result = audit_portfolio_risk(trades, max_portfolio_risk_pct=2.0, max_symbol_risk_pct=1.5)
        self.assertEqual(result["max_concurrent_positions"], 2)
        self.assertAlmostEqual(result["max_concurrent_stop_risk_pct"], 2.2)
        self.assertTrue(any(b["type"] == "portfolio_risk" for b in result["breaches"]))

    def test_same_symbol_risk_is_aggregated(self):
        trades = [
            {"symbol": "BTCUSDT", "timestamp": 1, "exit_timestamp": 10, "risk_pct": 0.6},
            {"symbol": "btcusdt", "timestamp": 2, "exit_timestamp": 9, "risk_pct": 0.6},
        ]
        result = audit_portfolio_risk(trades, max_portfolio_risk_pct=2.0, max_symbol_risk_pct=1.0)
        self.assertAlmostEqual(result["max_concurrent_stop_risk_by_symbol_pct"]["BTCUSDT"], 1.2)
        self.assertTrue(any(b["type"] == "symbol_risk" for b in result["breaches"]))

    def test_exit_at_entry_boundary_does_not_overlap_next_position(self):
        trades = [
            {"symbol": "BTCUSDT", "timestamp": 1, "exit_timestamp": 5, "risk_pct": 1.0},
            {"symbol": "ETHUSDT", "timestamp": 5, "exit_timestamp": 8, "risk_pct": 1.0},
        ]
        result = audit_portfolio_risk(trades, max_portfolio_risk_pct=1.0, max_symbol_risk_pct=1.0)
        self.assertEqual(result["max_concurrent_positions"], 1)
        self.assertEqual(result["breach_count"], 0)

    def test_invalid_trade_and_duplicate_ids_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "exits before entry"):
            audit_portfolio_risk([{"symbol": "BTC", "timestamp": 5, "exit_timestamp": 4, "risk_pct": 1}])
        with self.assertRaisesRegex(ValueError, "Duplicate trade_id"):
            audit_portfolio_risk([
                {"trade_id": "same", "symbol": "BTC", "timestamp": 1, "exit_timestamp": 2, "risk_pct": 0.5},
                {"trade_id": "same", "symbol": "ETH", "timestamp": 2, "exit_timestamp": 3, "risk_pct": 0.5},
            ])

    def test_invalid_limits_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "finite and positive"):
            audit_portfolio_risk([], max_portfolio_risk_pct=0)


if __name__ == "__main__":
    unittest.main()
