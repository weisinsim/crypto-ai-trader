import unittest

from research.funding_sensitivity import summarize_funding_scenarios


def result(trade_log, **overrides):
    value = {
        "status": "COMPLETED", "trades": len(trade_log),
        "net_return_pct": 1.25, "max_drawdown_pct": 4.5,
        "profit_factor": 1.2, "equity_floor_hit": False,
        "trade_log": trade_log,
    }
    value.update(overrides)
    return value


class FundingSensitivityTests(unittest.TestCase):
    def test_summarizes_cashflow_settlements_and_boundary_rate(self):
        report = summarize_funding_scenarios({
            "base": result([
                {"funding_cashflow": -2.5, "funding_settlement_count": 2, "funding_boundary_ambiguous": True},
                {"funding_cashflow": 1.0, "funding_settlement_count": 0, "funding_boundary_ambiguous": False},
            ]),
            "stress": result([
                {"funding_cashflow": -5.0, "funding_settlement_count": 2, "funding_boundary_ambiguous": True},
                {"funding_cashflow": -1.0, "funding_settlement_count": 1, "funding_boundary_ambiguous": False},
            ], net_return_pct=-0.5, max_drawdown_pct=8.0, profit_factor=0.8),
        })
        self.assertEqual(report["status"], "SUMMARY_ONLY_NOT_A_BACKTEST")
        base, stress = report["scenarios"]
        self.assertAlmostEqual(base["funding_cashflow_total"], -1.5)
        self.assertEqual(base["funding_settlement_count_total"], 2)
        self.assertEqual(base["trades_with_funding_settlements"], 1)
        self.assertEqual(base["ambiguous_boundary_trades"], 1)
        self.assertEqual(base["ambiguous_boundary_trade_pct"], 50.0)
        self.assertAlmostEqual(stress["funding_cashflow_total"], -6.0)
        self.assertLess(stress["net_return_pct"], base["net_return_pct"])
        self.assertEqual(len(report["limitations"]), 4)

    def test_empty_scenario_mapping_rejected(self):
        with self.assertRaisesRegex(ValueError, "non-empty"):
            summarize_funding_scenarios({})

    def test_mismatched_trade_count_rejected(self):
        with self.assertRaisesRegex(ValueError, "does not match"):
            summarize_funding_scenarios({"base": result([], trades=1)})

    def test_non_finite_cashflow_rejected(self):
        with self.assertRaisesRegex(ValueError, "funding_cashflow"):
            summarize_funding_scenarios({"base": result([{"funding_cashflow": float("nan")}])})

    def test_invalid_settlement_count_and_ambiguity_rejected(self):
        for trade in (
            {"funding_settlement_count": -1},
            {"funding_settlement_count": True},
            {"funding_boundary_ambiguous": 1},
        ):
            with self.subTest(trade=trade), self.assertRaises(ValueError):
                summarize_funding_scenarios({"base": result([trade])})

    def test_no_trades_has_no_fabricated_ambiguity_percentage(self):
        report = summarize_funding_scenarios({"base": result([], trades=0, profit_factor=None)})
        summary = report["scenarios"][0]
        self.assertIsNone(summary["ambiguous_boundary_trade_pct"])
        self.assertEqual(summary["funding_cashflow_total"], 0.0)
        self.assertIsNone(summary["profit_factor"])

    def test_insufficient_data_status_is_preserved(self):
        report = summarize_funding_scenarios({
            "short": result([], status="INSUFFICIENT_DATA", trades=0,
                            net_return_pct=0.0, max_drawdown_pct=0.0, profit_factor=None)
        })
        self.assertEqual(report["scenarios"][0]["status"], "INSUFFICIENT_DATA")


if __name__ == "__main__":
    unittest.main()
