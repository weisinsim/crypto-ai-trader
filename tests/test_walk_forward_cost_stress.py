import unittest

from research.run_backtest import summarize_walk_forward_cost_stress


class WalkForwardCostStressTests(unittest.TestCase):
    def _passing_fold(self):
        scenarios = {}
        for name in ("base", "stress", "severe_stress"):
            scenarios[name] = {
                "status": "COMPLETED",
                "net_return_pct": 2.0,
                "max_drawdown_pct": 5.0,
                "profit_factor": 1.3,
                "equity_floor_hit": False,
            }
        return {
            "fold": 1,
            "trades": 30,
            "cost_stress": {
                "scenarios": scenarios,
                "robustness_screen": {"pass": True},
            },
        }

    def test_complete_clean_fold_passes_research_gate(self):
        report = summarize_walk_forward_cost_stress([self._passing_fold()])
        self.assertTrue(report["walk_forward_candidate_gate"]["pass"])
        self.assertFalse(report["production_approval"])

    def test_missing_equity_floor_status_fails_closed(self):
        fold = self._passing_fold()
        del fold["cost_stress"]["scenarios"]["stress"]["equity_floor_hit"]
        report = summarize_walk_forward_cost_stress([fold])
        self.assertFalse(report["walk_forward_candidate_gate"]["pass"])
        self.assertEqual(report["folds_passing_all_three_cost_scenarios"], 0)

    def test_floor_hit_in_any_scenario_fails_gate(self):
        fold = self._passing_fold()
        fold["cost_stress"]["scenarios"]["severe_stress"]["equity_floor_hit"] = True
        report = summarize_walk_forward_cost_stress([fold])
        self.assertFalse(report["walk_forward_candidate_gate"]["pass"])

    def test_missing_cost_stress_is_not_counted_as_pass(self):
        report = summarize_walk_forward_cost_stress([{"fold": 1, "trades": 50}])
        self.assertFalse(report["walk_forward_candidate_gate"]["pass"])
        self.assertEqual(report["folds_with_missing_cost_stress"], 1)


if __name__ == "__main__":
    unittest.main()
