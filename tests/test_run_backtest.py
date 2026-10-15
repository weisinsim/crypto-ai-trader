import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research.run_backtest import main, summarize_walk_forward_cost_stress


class RunBacktestCostStressTests(unittest.TestCase):
    def test_cost_stress_is_reported_for_holdout_and_each_walk_forward_fold(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "candles.csv"
            output_path = Path(tmp) / "report.json"
            with csv_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["t", "o", "h", "l", "c", "v", "q"])
                writer.writeheader()
                for i in range(180):
                    close = 100 + (i % 24) * 0.8 + ((i // 24) % 2) * 3
                    open_price = close - (0.25 if i % 2 else -0.25)
                    writer.writerow({
                        "t": i * 3_600_000,
                        "o": open_price,
                        "h": max(open_price, close) + 1.2,
                        "l": min(open_price, close) - 1.2,
                        "c": close,
                        "v": 100,
                        "q": 100 * close,
                    })

            argv = [
                "run_backtest.py", "--csv", str(csv_path), "--symbol", "BTCUSDT",
                "--interval", "1h", "--train-fraction", "0.6", "--folds", "4",
                "--fast", "3", "--slow", "8", "--output", str(output_path),
            ]
            with patch.object(sys, "argv", argv):
                main()

            report = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "RESEARCH_ONLY")
            self.assertIn("scenarios", report["cost_stress"])
            self.assertEqual(len(report["walk_forward"]), 4)
            summary = report["walk_forward_cost_stress_summary"]
            self.assertEqual(summary["fold_count"], 4)
            self.assertEqual(summary["folds_passing_all_three_cost_scenarios"], sum(
                fold["cost_stress"]["robustness_screen"]["pass"]
                for fold in report["walk_forward"]
            ))
            self.assertEqual(summary["folds_with_missing_cost_stress"], 0)
            self.assertIn("worst_net_return_pct", summary["scenario_summary"]["stress"])
            self.assertFalse(summary["production_approval"])
            for fold in report["walk_forward"]:
                self.assertIn("cost_stress", fold)
                self.assertEqual(
                    list(fold["cost_stress"]["scenarios"]),
                    ["base", "stress", "severe_stress"],
                )
                self.assertEqual(fold["cost_stress"]["evaluation_window"]["start_ts"], fold["start_ts"])
                self.assertFalse(fold["cost_stress"]["robustness_screen"]["production_approval"])


class WalkForwardCostStressSummaryTests(unittest.TestCase):
    def test_missing_or_failed_fold_is_not_counted_as_passing(self):
        folds = [
            {
                "cost_stress": {
                    "robustness_screen": {"pass": True},
                    "scenarios": {
                        "base": {"status": "COMPLETED", "net_return_pct": 2.0,
                                 "max_drawdown_pct": 3.0, "profit_factor": 1.4},
                        "stress": {"status": "COMPLETED", "net_return_pct": 1.0,
                                   "max_drawdown_pct": 4.0, "profit_factor": 1.2},
                        "severe_stress": {"status": "COMPLETED", "net_return_pct": 0.2,
                                          "max_drawdown_pct": 5.0, "profit_factor": 1.16},
                    },
                },
            },
            {"cost_stress": {"robustness_screen": {"pass": False}, "scenarios": {}}},
            {},
        ]
        summary = summarize_walk_forward_cost_stress(folds)
        self.assertEqual(summary["fold_count"], 3)
        self.assertEqual(summary["folds_passing_all_three_cost_scenarios"], 1)
        self.assertEqual(summary["folds_with_missing_cost_stress"], 1)
        self.assertEqual(summary["scenario_summary"]["base"]["folds_completed"], 1)
        self.assertFalse(summary["production_approval"])

    def test_infinite_pf_is_excluded_from_numeric_minimum(self):
        folds = [
            {"cost_stress": {"scenarios": {
                "base": {"status": "COMPLETED", "net_return_pct": 1.0,
                         "max_drawdown_pct": 2.0, "profit_factor": "INF"},
                "stress": {}, "severe_stress": {},
            }}},
            {"cost_stress": {"scenarios": {
                "base": {"status": "COMPLETED", "net_return_pct": -1.0,
                         "max_drawdown_pct": 6.0, "profit_factor": 1.25},
                "stress": {}, "severe_stress": {},
            }}},
        ]
        summary = summarize_walk_forward_cost_stress(folds)
        base = summary["scenario_summary"]["base"]
        self.assertEqual(base["lowest_numeric_profit_factor"], 1.25)
        self.assertEqual(base["worst_net_return_pct"], -1.0)
        self.assertEqual(base["worst_max_drawdown_pct"], 6.0)


    def test_candidate_gate_requires_all_folds_and_minimum_trade_count(self):
        passing_stress = {
            "robustness_screen": {"pass": True},
            "scenarios": {
                name: {"status": "COMPLETED", "net_return_pct": 1.0,
                       "max_drawdown_pct": 3.0, "profit_factor": 1.3}
                for name in ("base", "stress", "severe_stress")
            },
        }
        summary = summarize_walk_forward_cost_stress([
            {"fold": 1, "trades": 35, "cost_stress": passing_stress},
            {"fold": 2, "trades": 12, "cost_stress": passing_stress},
        ])
        gate = summary["walk_forward_candidate_gate"]
        self.assertFalse(gate["pass"])
        self.assertEqual(gate["under_sampled_folds"], [2])
        self.assertFalse(gate["promotion"])

    def test_candidate_gate_rejects_missing_scenario_even_if_flagged_passing(self):
        good = {
            "robustness_screen": {"pass": True},
            "scenarios": {
                "base": {"status": "COMPLETED", "net_return_pct": 1.0,
                         "max_drawdown_pct": 3.0, "profit_factor": 1.3},
                "stress": {"status": "COMPLETED", "net_return_pct": 0.5,
                           "max_drawdown_pct": 4.0, "profit_factor": 1.2},
            },
        }
        summary = summarize_walk_forward_cost_stress([
            {"fold": 1, "trades": 40, "cost_stress": good},
        ])
        gate = summary["walk_forward_candidate_gate"]
        self.assertFalse(gate["pass"])
        self.assertEqual(gate["folds_passing_all_three_cost_scenarios"], 0)
        self.assertEqual(gate["folds_with_missing_cost_stress"], 1)

    def test_candidate_gate_rejects_non_finite_metrics(self):
        import math
        invalid = {
            "robustness_screen": {"pass": True},
            "scenarios": {
                name: {"status": "COMPLETED", "net_return_pct": 1.0,
                       "max_drawdown_pct": 3.0, "profit_factor": 1.3}
                for name in ("base", "stress", "severe_stress")
            },
        }
        invalid["scenarios"]["stress"]["net_return_pct"] = math.nan
        summary = summarize_walk_forward_cost_stress([
            {"fold": 1, "trades": 40, "cost_stress": invalid},
        ])
        gate = summary["walk_forward_candidate_gate"]
        self.assertFalse(gate["pass"])
        self.assertEqual(gate["folds_passing_all_three_cost_scenarios"], 0)

    def test_candidate_gate_fails_when_any_fold_fails_cost_stress(self):
        good = {"robustness_screen": {"pass": True}, "scenarios": {}}
        bad = {"robustness_screen": {"pass": False}, "scenarios": {}}
        summary = summarize_walk_forward_cost_stress([
            {"fold": 1, "trades": 40, "cost_stress": good},
            {"fold": 2, "trades": 45, "cost_stress": bad},
        ])
        gate = summary["walk_forward_candidate_gate"]
        self.assertFalse(gate["pass"])
        self.assertFalse(gate["all_folds_stress_pass"])


if __name__ == "__main__":
    unittest.main()
