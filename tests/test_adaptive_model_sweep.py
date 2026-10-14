import unittest

from research.adaptive_model_sweep import passes_cost_robustness


class AdaptiveModelSweepGateTests(unittest.TestCase):
    def test_cost_gate_requires_all_tiers_and_explicit_floor_status(self):
        good = {"net_return_pct": 2.0, "profit_factor": 1.2, "equity_floor_hit": False}
        floor_hit = {"net_return_pct": 4.0, "profit_factor": 2.0, "equity_floor_hit": True}
        self.assertTrue(passes_cost_robustness([good, good, good]))
        self.assertFalse(passes_cost_robustness([good, good, floor_hit]))
        severe_loss = {"net_return_pct": -0.5, "profit_factor": 0.9, "equity_floor_hit": False}
        self.assertFalse(passes_cost_robustness([good, good, severe_loss]))
        self.assertFalse(passes_cost_robustness([{"net_return_pct": 2.0, "profit_factor": 1.2}]))

    def test_cost_gate_rejects_bad_profit_factor_and_non_finite_return(self):
        self.assertFalse(passes_cost_robustness([
            {"net_return_pct": 2.0, "profit_factor": 1.0, "equity_floor_hit": False}
        ]))
        self.assertFalse(passes_cost_robustness([
            {"net_return_pct": float("nan"), "profit_factor": 2.0, "equity_floor_hit": False}
        ]))


if __name__ == "__main__":
    unittest.main()
