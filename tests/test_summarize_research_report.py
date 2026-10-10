import unittest

from validation.summarize_research_report import render


class ResearchReportTests(unittest.TestCase):
    def test_renders_holdout_metrics_for_both_targets_and_modes(self):
        manifest = {
            "min_trades_threshold": 30,
            "historical_funding_required": True,
            "cost_sensitivity_bps": [5, 10],
            "reports": [{
                "symbol": "BTCUSDT", "cost_bps": 5, "status": "RESEARCH_ONLY",
                "holdout_min_trade_count_across_exit_variants": 31,
                "holdout_max_trade_count_across_exit_variants": 38,
                "development_metrics_by_exit": {
                    "target_2.5R": {
                        "signal_level": {"trades": 80, "profit_factor": 1.4, "net_R": 12.0},
                        "single_position": {"trades": 40, "profit_factor": 1.2, "net_R": 5.0},
                    },
                },
                "holdout_metrics_by_exit": {
                    "target_2.5R": {
                        "signal_level": {"trades": 38, "win_rate_pct": 55.0, "profit_factor": 1.2, "net_R": 4.2, "avg_R": 0.11, "max_drawdown_R": 2.0, "max_win_R": 2.1, "largest_win_share_pct": 22.0, "top_three_wins_share_pct": 48.0},
                        "single_position": {"trades": 31, "win_rate_pct": 54.0, "profit_factor": 1.1, "net_R": 3.1, "avg_R": 0.1, "max_drawdown_R": 2.2},
                    },
                    "target_3.0R": {
                        "signal_level": {"trades": 37, "win_rate_pct": 50.0, "profit_factor": 1.3, "net_R": 4.5, "avg_R": 0.12, "max_drawdown_R": 2.1},
                        "single_position": {"trades": 30, "win_rate_pct": 49.0, "profit_factor": 1.2, "net_R": 3.3, "avg_R": 0.11, "max_drawdown_R": 2.4},
                    },
                },
            }],
        }
        report = render(manifest)
        self.assertIn("BTCUSDT", report)
        self.assertIn("2.5R", report)
        self.assertIn("3.0R", report)
        self.assertIn("signal_level", report)
        self.assertIn("single_position", report)
        self.assertIn("4.5000", report)
        self.assertIn("Development vs Holdout comparison", report)
        self.assertIn("Development/Holdout stability flags", report)
        self.assertIn("80 | 1.4000 | 12.0000", report)
        self.assertIn("not a full portfolio simulator", report)
        self.assertIn("Largest win %", report)
        self.assertIn("22.00", report)
        self.assertIn("48.00", report)

    def test_non_research_status_is_listed_but_not_misrepresented_as_metrics(self):
        report = render({"reports": [{"symbol": "ETHUSDT", "cost_bps": 10, "status": "NO_SIGNALS"}]})
        self.assertIn("NO_SIGNALS", report)
        self.assertNotIn("| ETHUSDT | 10 | 2.5R |", report)

    def test_empty_manifest_is_safe(self):
        report = render({"reports": []})
        self.assertIn("Historical multi-asset research summary", report)
        self.assertIn("Holdout metrics by cost", report)

    def test_missing_metrics_render_as_dashes(self):
        report = render({"reports": [{
            "symbol": "SOLUSDT", "cost_bps": 20, "status": "RESEARCH_ONLY",
            "holdout_metrics_by_exit": {"target_2.5R": {"signal_level": {"trades": 0}}}
        }]})
        self.assertIn("—", report)


    def test_flags_opposite_development_and_holdout_signs(self):
        manifest = {"reports": [
            {"symbol": "BTCUSDT", "cost_bps": 10, "status": "RESEARCH_ONLY",
             "development_metrics_by_exit": {"target_3.0R": {
                 "single_position": {"net_R": -4.2}}},
             "holdout_metrics_by_exit": {"target_3.0R": {
                 "single_position": {"net_R": 9.2}}}},
            {"symbol": "AVAXUSDT", "cost_bps": 10, "status": "RESEARCH_ONLY",
             "development_metrics_by_exit": {"target_3.0R": {
                 "single_position": {"net_R": 8.4}}},
             "holdout_metrics_by_exit": {"target_3.0R": {
                 "single_position": {"net_R": -3.6}}}},
        ]}
        report = render(manifest)
        self.assertIn("REGIME_INSTABILITY: dev loss / holdout gain", report)
        self.assertIn("GENERALIZATION_FAILURE: dev gain / holdout loss", report)


if __name__ == "__main__":
    unittest.main()
