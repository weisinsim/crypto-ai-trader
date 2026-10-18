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
            "status": "COMPLETED",
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

    def test_inf_profit_factor_is_valid_for_threshold_gate(self):
        fold = self._passing_fold()
        for scenario in fold["cost_stress"]["scenarios"].values():
            scenario["profit_factor"] = "INF"
        report = summarize_walk_forward_cost_stress([fold])
        self.assertTrue(report["walk_forward_candidate_gate"]["pass"])
        self.assertEqual(report["scenario_summary"]["base"]["folds_pf_above_1_15"], 1)
        self.assertIsNone(report["scenario_summary"]["base"]["lowest_numeric_profit_factor"])

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
        report = summarize_walk_forward_cost_stress([{"fold": 1, "status": "COMPLETED", "trades": 50}])
        self.assertFalse(report["walk_forward_candidate_gate"]["pass"])
        self.assertEqual(report["folds_with_missing_cost_stress"], 1)

    def test_non_completed_fold_fails_closed_even_with_passing_stress_rows(self):
        fold = self._passing_fold()
        fold["status"] = "INSUFFICIENT_DATA"
        report = summarize_walk_forward_cost_stress([fold])
        self.assertFalse(report["walk_forward_candidate_gate"]["pass"])
        self.assertEqual(report["folds_passing_all_three_cost_scenarios"], 0)

    def test_incomplete_fold_does_not_inflate_positive_return_or_pf_counts(self):
        fold = self._passing_fold()
        fold["status"] = "INSUFFICIENT_DATA"
        report = summarize_walk_forward_cost_stress([fold])
        for scenario in ("base", "stress", "severe_stress"):
            with self.subTest(scenario=scenario):
                summary = report["scenario_summary"][scenario]
                self.assertEqual(summary["folds_positive_return"], 0)
                self.assertEqual(summary["folds_pf_above_1_15"], 0)

    def test_scenario_summary_does_not_count_incomplete_underlying_fold(self):
        fold = self._passing_fold()
        fold["status"] = "INSUFFICIENT_DATA"
        report = summarize_walk_forward_cost_stress([fold])
        self.assertEqual(report["scenario_summary"]["base"]["folds_reported"], 1)
        self.assertEqual(report["scenario_summary"]["base"]["folds_completed"], 0)

    def test_incomplete_fold_metrics_do_not_distort_completed_fold_extrema(self):
        complete = self._passing_fold()
        incomplete = self._passing_fold()
        incomplete["fold"] = 2
        incomplete["status"] = "INSUFFICIENT_DATA"
        for row in incomplete["cost_stress"]["scenarios"].values():
            row["net_return_pct"] = -99.0
            row["max_drawdown_pct"] = 99.0
            row["profit_factor"] = 0.01
        report = summarize_walk_forward_cost_stress([complete, incomplete])
        summary = report["scenario_summary"]["base"]
        self.assertEqual(summary["folds_reported"], 2)
        self.assertEqual(summary["folds_completed"], 1)
        self.assertEqual(summary["worst_net_return_pct"], 2.0)
        self.assertEqual(summary["worst_max_drawdown_pct"], 5.0)
        self.assertEqual(summary["lowest_numeric_profit_factor"], 1.3)


if __name__ == "__main__":
    unittest.main()
