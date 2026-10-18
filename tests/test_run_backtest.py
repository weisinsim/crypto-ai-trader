import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research.run_backtest import load_csv, load_funding_csv, main, max_drawdown_pct, summarize_funding_data_quality, summarize_holdout_trade_quality, summarize_walk_forward_cost_stress


class FundingDataQualitySummaryTests(unittest.TestCase):
    def test_reports_timestamp_alignment_without_claiming_completeness(self):
        candles = [{"t": 100}, {"t": 200}, {"t": 300}, {"t": 400}]
        funding = [
            {"funding_time": 100, "mark_price_source": "reported", "venue": "binance_usdm"},
            {"funding_time": 250, "mark_price_source": "proxy", "venue": "binance_usdm"},
            {"funding_time": 400, "mark_price_source": "reported", "venue": "binance_usdm"},
        ]
        summary = summarize_funding_data_quality(funding, candles)
        self.assertEqual(summary["records_in_candle_range"], 3)
        self.assertEqual(summary["exact_candle_open_matches"], 2)
        self.assertEqual(summary["non_candle_open_events"], 1)
        self.assertEqual(summary["source_labels"], ["proxy", "reported"])
        self.assertFalse(summary["event_completeness_verified"])
        self.assertTrue(summary["review_required"])
        self.assertEqual(summary["audit_status"], "REVIEW_REQUIRED")
        self.assertIn("do not prove event completeness", summary["interpretation"])

    def test_normalizes_only_small_jitter_for_alignment_but_preserves_raw_timestamp(self):
        candles = [{"t": 100}, {"t": 200}, {"t": 300}, {"t": 400}]
        funding = [
            {"funding_time": 100, "funding_time_raw": 101,
             "mark_price_source": "reported", "venue": "binance_usdm"},
            {"funding_time": 400, "funding_time_raw": 400,
             "mark_price_source": "reported", "venue": "binance_usdm"},
        ]
        summary = summarize_funding_data_quality(funding, candles)
        self.assertEqual(summary["exact_candle_open_matches"], 1)
        self.assertEqual(summary["normalized_candle_open_matches"], 2)
        self.assertEqual(summary["boundary_jitter_normalized_records"], 1)
        self.assertEqual(summary["non_candle_open_events"], 0)
        self.assertEqual(summary["first_event_ts"], 101)
        self.assertFalse(summary["review_required"])
        self.assertEqual(
            summary["audit_status"],
            "BOUNDARY_JITTER_NORMALIZED_ALIGNMENT_PASS_NOT_RECONCILED",
        )
        self.assertFalse(summary["event_completeness_verified"])

    def test_proxy_mark_price_requires_review_even_when_timestamps_align(self):
        candles = [{"t": 100}, {"t": 200}, {"t": 300}]
        funding = [
            {"funding_time": 100, "mark_price_source": "archive_prior_closed_mark_price_1h_proxy_NOT_SETTLEMENT_MARK", "venue": "binance_usdm"},
            {"funding_time": 300, "mark_price_source": "archive_prior_closed_mark_price_1h_proxy_NOT_SETTLEMENT_MARK", "venue": "binance_usdm"},
        ]
        summary = summarize_funding_data_quality(funding, candles)
        self.assertFalse(summary["review_required"])
        self.assertTrue(summary["proxy_mark_price_review_required"])
        self.assertEqual(summary["proxy_mark_price_records"], 2)
        self.assertEqual(summary["source_record_counts"], {
            "archive_prior_closed_mark_price_1h_proxy_NOT_SETTLEMENT_MARK": 2
        })
        self.assertIn("PROXY", summary["source_labels"][0].upper())

    def test_missing_or_blank_provenance_labels_require_review(self):
        candles = [{"t": 100}, {"t": 200}, {"t": 300}]
        funding = [
            {"funding_time": 100, "funding_rate": 0.001, "mark_price": 100,
             "mark_price_source": "", "venue": "binance_usdm"},
            {"funding_time": 300, "funding_rate": 0.001, "mark_price": 100,
             "mark_price_source": "reported", "venue": ""},
        ]
        summary = summarize_funding_data_quality(funding, candles)
        self.assertEqual(summary["records_missing_or_invalid_source_label"], 1)
        self.assertEqual(summary["records_missing_or_invalid_venue_label"], 1)
        self.assertTrue(summary["review_required"])
        self.assertEqual(summary["audit_status"], "REVIEW_REQUIRED")

    def test_funding_csv_loader_reports_missing_required_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "funding.csv"
            path.write_text("symbol,funding_time,funding_rate\\nBTCUSDT,0,0.001\\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "missing required columns.*mark_price"):
                load_funding_csv(str(path))

    def test_funding_csv_loader_preserves_missing_provenance_instead_of_inventing_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "funding.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["symbol", "funding_time", "funding_rate", "mark_price"])
                writer.writeheader()
                writer.writerow({"symbol": "BTCUSDT", "funding_time": 0, "funding_rate": 0.001, "mark_price": 100})
            loaded = load_funding_csv(str(path))
            self.assertEqual(loaded[0]["mark_price_source"], "")
            self.assertEqual(loaded[0]["venue"], "")
            summary = summarize_funding_data_quality(loaded, [{"t": 0}, {"t": 1}])
            self.assertEqual(summary["records_missing_or_invalid_source_label"], 1)
            self.assertEqual(summary["records_missing_or_invalid_venue_label"], 1)
            self.assertTrue(summary["review_required"])

    def test_funding_pnl_authority_is_explicitly_blocked(self):
        candles = [{"t": i * 3_600_000, "o": 100, "h": 101, "l": 99, "c": 100, "v": 1, "q": 100} for i in range(1400)]
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "candles.csv"
            output_path = Path(tmp) / "report.json"
            with csv_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["t", "o", "h", "l", "c", "v", "q"])
                writer.writeheader()
                writer.writerows(candles)
            argv = ["run_backtest.py", "--csv", str(csv_path), "--symbol", "BTCUSDT", "--interval", "1h", "--output", str(output_path)]
            with patch.object(sys, "argv", argv):
                main()
            report = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertFalse(report["data_lineage"]["funding_pnl_authoritative"])
            self.assertEqual(len(report["data_lineage"]["funding_pnl_authority_blockers"]), 3)

    def test_report_funding_gate_blocks_proxy_marks_even_when_timestamps_align(self):
        candles = [{"t": i * 3_600_000, "o": 100, "h": 101, "l": 99, "c": 100, "v": 1, "q": 100} for i in range(1400)]
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "candles.csv"
            funding_path = Path(tmp) / "funding.csv"
            output_path = Path(tmp) / "report.json"
            with csv_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["t", "o", "h", "l", "c", "v", "q"])
                writer.writeheader()
                writer.writerows(candles)
            with funding_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["symbol", "funding_time", "funding_rate", "mark_price", "mark_price_source", "venue"])
                writer.writeheader()
                writer.writerow({"symbol": "BTCUSDT", "funding_time": 0, "funding_rate": 0.0001, "mark_price": 100, "mark_price_source": "archive_prior_closed_mark_price_1h_proxy_NOT_SETTLEMENT_MARK", "venue": "binance_usdm"})
            argv = ["run_backtest.py", "--csv", str(csv_path), "--funding-csv", str(funding_path), "--symbol", "BTCUSDT", "--interval", "1h", "--output", str(output_path)]
            with patch.object(sys, "argv", argv):
                main()
            report = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(report["funding_interpretation_gate"], "FUNDING_DATA_REVIEW_REQUIRED")
            self.assertTrue(report["funding_data"]["proxy_mark_price_review_required"])
            self.assertFalse(report["data_lineage"]["funding_pnl_authoritative"])

    def test_report_rejects_funding_csv_for_different_symbol(self):
        candles = [{"t": i * 3_600_000, "o": 100, "h": 101, "l": 99, "c": 100, "v": 1, "q": 100} for i in range(1400)]
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "candles.csv"
            funding_path = Path(tmp) / "funding.csv"
            output_path = Path(tmp) / "report.json"
            with csv_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["t", "o", "h", "l", "c", "v", "q"])
                writer.writeheader()
                writer.writerows(candles)
            with funding_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["symbol", "funding_time", "funding_rate", "mark_price", "mark_price_source", "venue"])
                writer.writeheader()
                writer.writerow({"symbol": "ETHUSDT", "funding_time": 0, "funding_rate": 0.0001, "mark_price": 100, "mark_price_source": "test_fixture", "venue": "binance_usdm"})
            argv = ["run_backtest.py", "--csv", str(csv_path), "--symbol", "BTCUSDT", "--interval", "1h", "--funding-csv", str(funding_path), "--output", str(output_path)]
            with patch.object(sys, "argv", argv):
                with self.assertRaisesRegex(ValueError, "funding CSV symbol does not match --symbol"):
                    main()
            self.assertFalse(output_path.exists())

    def test_missing_funding_is_explicitly_unverified(self):
        summary = summarize_funding_data_quality(None, [{"t": 100}])
        self.assertFalse(summary["provided"])
        self.assertEqual(summary["records_in_candle_range"], 0)
        self.assertFalse(summary["event_completeness_verified"])
        self.assertTrue(summary["review_required"])
        self.assertEqual(summary["audit_status"], "REVIEW_REQUIRED")

    def test_uses_sorted_raw_timestamps_and_flags_duplicates_and_out_of_range_events(self):
        candles = [{"t": 100}, {"t": 200}, {"t": 300}]
        funding = [
            {"funding_time": 100, "funding_time_raw": 200, "mark_price_source": "reported"},
            {"funding_time": 200, "funding_time_raw": 200, "mark_price_source": "reported"},
            {"funding_time": 300, "funding_time_raw": 400, "mark_price_source": "reported"},
        ]
        summary = summarize_funding_data_quality(funding, candles)
        self.assertEqual(summary["first_event_ts"], 200)
        self.assertEqual(summary["last_event_ts"], 400)
        self.assertEqual(summary["records_in_candle_range"], 2)
        self.assertEqual(summary["records_outside_candle_range"], 1)
        self.assertEqual(summary["duplicate_event_records"], 1)
        self.assertEqual(summary["duplicate_event_timestamps"], [200])
        self.assertTrue(summary["review_required"])
        self.assertEqual(summary["audit_status"], "REVIEW_REQUIRED")
        self.assertEqual(summary["event_interval_ms"]["min"], 0)



class HoldoutTradeQualityTests(unittest.TestCase):
    def test_groups_holdout_trades_without_overstating_cost_attribution(self):
        trades = [
            {"side": "LONG", "reason": "TAKE_PROFIT", "bars_held": 4, "pnl_equity": 0.03,
             "funding_cashflow": -0.001, "funding_boundary_ambiguous": False},
            {"side": "LONG", "reason": "STOP_LOSS", "bars_held": 2, "pnl_equity": -0.02,
             "funding_cashflow": 0.0, "funding_boundary_ambiguous": True},
            {"side": "SHORT", "reason": "STOP_LOSS", "bars_held": 15, "pnl_equity": -0.01,
             "funding_cashflow": 0.002, "funding_boundary_ambiguous": False},
        ]
        summary = summarize_holdout_trade_quality(trades)
        self.assertEqual(summary["status"], "DIAGNOSTIC_ONLY")
        self.assertEqual(summary["by_side"]["LONG"]["trades"], 2)
        self.assertEqual(summary["by_side"]["LONG"]["wins"], 1)
        self.assertEqual(summary["by_side"]["LONG"]["losses"], 1)
        self.assertEqual(summary["by_exit_reason"]["STOP_LOSS"]["trades"], 2)
        self.assertEqual(summary["by_holding_duration"]["13_plus_bars"]["trades"], 1)
        self.assertEqual(summary["by_side"]["LONG"]["funding_boundary_ambiguous_trades"], 1)
        self.assertIn("fee and slippage attribution is not separately reconstructed", summary["limitations"][0])


class RunBacktestCostStressTests(unittest.TestCase):
    def test_invalid_cost_stress_multipliers_are_rejected(self):
        rows = [
            {"t": i * 3_600_000, "o": 100.0, "h": 102.0, "l": 98.0,
             "c": 100.0, "v": 1.0, "q": 100.0}
            for i in range(180)
        ]
        cases = [
            (["--stress-multiplier", "nan"], "must be finite"),
            (["--stress-multiplier", "1.5", "--severe-stress-multiplier", "1.2"],
             "1 <= stress <= severe-stress"),
            (["--stress-multiplier", "0.9"], "1 <= stress <= severe-stress"),
        ]
        for extra_args, message in cases:
            argv = [
                "run_backtest.py", "--csv", "unused.csv", "--symbol", "BTCUSDT",
                "--interval", "1h", "--fast", "3", "--slow", "8",
                "--output", "unused-report.json", *extra_args,
            ]
            with self.subTest(extra_args=extra_args), patch.object(sys, "argv", argv), \
                    patch("research.run_backtest.load_csv", return_value=rows):
                with self.assertRaisesRegex(ValueError, message):
                    main()

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
            boundary = report["funding_boundary_policy_sensitivity"]
            self.assertEqual(boundary["status"], "SUMMARY_ONLY_NOT_A_BACKTEST")
            self.assertEqual(boundary["scenario_count"], 2)
            policies = {row["scenario"]: row for row in boundary["scenarios"]}
            self.assertEqual(set(policies), {"exclude", "include"})
            self.assertEqual(policies["exclude"]["trades"], policies["include"]["trades"])
            self.assertEqual(
                boundary["comparison"]["net_return_delta_include_minus_exclude_pct"], 0.0
            )
            self.assertIn("neither policy establishes actual exchange", boundary["comparison"]["interpretation"])
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
                fold_funding = fold["funding_boundary_policy_sensitivity"]
                self.assertEqual(fold_funding["status"], "SUMMARY_ONLY_NOT_A_BACKTEST")
                self.assertEqual(fold_funding["scenario_count"], 2)
                fold_policies = {row["scenario"]: row for row in fold_funding["scenarios"]}
                self.assertEqual(set(fold_policies), {"exclude", "include"})
                self.assertEqual(
                    fold_policies["exclude"]["trades"],
                    fold_policies["include"]["trades"],
                )
                self.assertIn(
                    "neither policy establishes actual exchange",
                    fold_funding["comparison"]["interpretation"],
                )


    def test_funding_entry_boundary_policy_changes_ledger_with_exact_timestamp_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "candles.csv"
            funding_path = Path(tmp) / "funding.csv"
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

            base_args = [
                "run_backtest.py", "--csv", str(csv_path), "--symbol", "BTCUSDT",
                "--interval", "1h", "--train-fraction", "0.6", "--folds", "4",
                "--fast", "3", "--slow", "8", "--output", str(output_path),
            ]
            with patch.object(sys, "argv", base_args):
                main()
            baseline = json.loads(output_path.read_text(encoding="utf-8"))
            baseline_scenarios = {
                row["scenario"]: row
                for row in baseline["funding_boundary_policy_sensitivity"]["scenarios"]
            }
            entries = baseline_scenarios["exclude"]["trades"]
            self.assertGreater(entries, 0)
            # Re-run once per actual holdout entry timestamp so the synthetic event
            # tests the exact ambiguous boundary, not a guessed candle timestamp.
            baseline_report_trades = baseline["funding_boundary_policy_sensitivity"]["scenarios"]
            # The detailed trade log is intentionally not exposed in summary rows;
            # use the same deterministic EMA run to derive the first holdout entry.
            from research.backtest import run_ema_cross_backtest
            from research.run_backtest import load_csv
            candles = load_csv(str(csv_path), 3_600_000)
            split_ts = candles[int(len(candles) * 0.6)]["t"]
            raw = run_ema_cross_backtest(candles, fast=3, slow=8, trade_start_ts=split_ts)
            entry_ts = next(t["entry_time"] for t in raw["trade_log"] if t["entry_time"] >= split_ts)
            with funding_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=[
                    "funding_time", "funding_rate", "mark_price", "symbol",
                    "mark_price_source", "venue",
                ])
                writer.writeheader()
                writer.writerow({
                    "funding_time": entry_ts, "funding_rate": 0.01,
                    "mark_price": 100.0, "symbol": "BTCUSDT",
                    "mark_price_source": "synthetic-test-only", "venue": "synthetic",
                })

            with patch.object(sys, "argv", [*base_args, "--funding-csv", str(funding_path)]):
                main()
            report = json.loads(output_path.read_text(encoding="utf-8"))
            boundary = report["funding_boundary_policy_sensitivity"]
            policies = {row["scenario"]: row for row in boundary["scenarios"]}
            self.assertEqual(policies["exclude"]["trades"], policies["include"]["trades"])
            self.assertEqual(policies["exclude"]["funding_settlement_count_total"], 0)
            self.assertGreater(policies["include"]["funding_settlement_count_total"], 0)
            self.assertNotEqual(
                policies["exclude"]["funding_cashflow_total"],
                policies["include"]["funding_cashflow_total"],
            )
            self.assertNotEqual(
                boundary["comparison"]["net_return_delta_include_minus_exclude_pct"], 0.0
            )
            self.assertIn("synthetic", report["funding_csv_manifest"]["venue"] if "venue" in report.get("funding_csv_manifest", {}) else "synthetic")


class DrawdownMetricValidationTests(unittest.TestCase):
    def test_oversized_or_invalid_equity_values_fail_closed(self):
        for initial in (10 ** 1000, None, float("inf")):
            with self.subTest(initial=initial), self.assertRaisesRegex(ValueError, "initial_equity"):
                max_drawdown_pct([], initial_equity=initial)
        for points in (
            [{"equity": 10 ** 1000}],
            [{"equity": float("nan")}],
            [None],
            [{}],
        ):
            with self.subTest(points=points), self.assertRaises(ValueError):
                max_drawdown_pct(points)

    def test_drawdown_includes_starting_equity(self):
        self.assertEqual(max_drawdown_pct([{"equity": 0.8}, {"equity": 1.2}, {"equity": 0.9}]), 25.0)


class WalkForwardCostStressSummaryTests(unittest.TestCase):
    def test_malformed_fold_record_is_rejected_without_crashing(self):
        summary = summarize_walk_forward_cost_stress([
            None,
            {"fold": 2, "trades": 40, "cost_stress": {}},
        ])
        gate = summary["walk_forward_candidate_gate"]
        self.assertFalse(gate["pass"])
        self.assertEqual(gate["under_sampled_folds"], [None])
        self.assertEqual(summary["folds_passing_all_three_cost_scenarios"], 0)

    def test_malformed_scenarios_and_rows_fail_closed_without_crashing(self):
        summary = summarize_walk_forward_cost_stress([
            {"fold": 1, "trades": 40, "cost_stress": {"scenarios": None}},
            {"fold": 2, "trades": 40, "cost_stress": {"scenarios": {"base": None}}},
        ])
        self.assertFalse(summary["walk_forward_candidate_gate"]["pass"])
        self.assertEqual(summary["folds_passing_all_three_cost_scenarios"], 0)
        self.assertEqual(summary["folds_with_missing_cost_stress"], 2)
        self.assertEqual(summary["scenario_summary"]["base"]["folds_completed"], 0)

    def test_missing_or_failed_fold_is_not_counted_as_passing(self):
        folds = [
            {
                "status": "COMPLETED",
                "cost_stress": {
                    "robustness_screen": {"pass": True},
                    "scenarios": {
                        "base": {"status": "COMPLETED", "net_return_pct": 2.0,
                                 "max_drawdown_pct": 3.0, "profit_factor": 1.4,
                                 "equity_floor_hit": False},
                        "stress": {"status": "COMPLETED", "net_return_pct": 1.0,
                                   "max_drawdown_pct": 4.0, "profit_factor": 1.2,
                                   "equity_floor_hit": False},
                        "severe_stress": {"status": "COMPLETED", "net_return_pct": 0.2,
                                          "max_drawdown_pct": 5.0, "profit_factor": 1.16,
                                          "equity_floor_hit": False},
                    },
                },
            },
            {"cost_stress": {"robustness_screen": {"pass": False}, "scenarios": {}}},
            {},
        ]
        summary = summarize_walk_forward_cost_stress(folds)
        self.assertEqual(summary["fold_count"], 3)
        self.assertEqual(summary["folds_passing_all_three_cost_scenarios"], 1)
        self.assertEqual(summary["folds_with_missing_cost_stress"], 2)
        self.assertEqual(summary["scenario_summary"]["base"]["folds_completed"], 1)
        self.assertFalse(summary["production_approval"])

    def test_infinite_pf_is_excluded_from_numeric_minimum(self):
        folds = [
            {"status": "COMPLETED", "cost_stress": {"scenarios": {
                "base": {"status": "COMPLETED", "net_return_pct": 1.0,
                         "max_drawdown_pct": 2.0, "profit_factor": "INF"},
                "stress": {}, "severe_stress": {},
            }}},
            {"status": "COMPLETED", "cost_stress": {"scenarios": {
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
        self.assertEqual(summary["folds_passing_all_three_cost_scenarios"], 0)
        self.assertEqual(summary["folds_with_missing_cost_stress"], 1)

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
        self.assertEqual(summary["folds_passing_all_three_cost_scenarios"], 0)

    def test_candidate_gate_rejects_passing_flag_with_failed_scenario_metrics(self):
        contradictory = {
            "robustness_screen": {"pass": True},
            "scenarios": {
                "base": {"status": "COMPLETED", "net_return_pct": 1.0,
                         "max_drawdown_pct": 3.0, "profit_factor": 1.3},
                "stress": {"status": "COMPLETED", "net_return_pct": -0.1,
                           "max_drawdown_pct": 4.0, "profit_factor": 1.2},
                "severe_stress": {"status": "COMPLETED", "net_return_pct": 0.2,
                                  "max_drawdown_pct": 5.0, "profit_factor": 1.16},
            },
        }
        summary = summarize_walk_forward_cost_stress([
            {"fold": 1, "trades": 40, "cost_stress": contradictory},
        ])
        gate = summary["walk_forward_candidate_gate"]
        self.assertFalse(gate["pass"])
        self.assertEqual(summary["folds_passing_all_three_cost_scenarios"], 0)

    def test_candidate_gate_rejects_equity_floor_hit_even_if_flagged_passing(self):
        floor_hit = {
            "robustness_screen": {"pass": True},
            "scenarios": {
                name: {"status": "COMPLETED", "net_return_pct": 1.0,
                       "max_drawdown_pct": 3.0, "profit_factor": 1.3}
                for name in ("base", "stress", "severe_stress")
            },
        }
        floor_hit["scenarios"]["severe_stress"]["equity_floor_hit"] = True
        summary = summarize_walk_forward_cost_stress([
            {"fold": 1, "trades": 40, "cost_stress": floor_hit},
        ])
        gate = summary["walk_forward_candidate_gate"]
        self.assertFalse(gate["pass"])
        self.assertEqual(summary["folds_passing_all_three_cost_scenarios"], 0)

    def test_candidate_gate_rejects_contradictory_stress_metrics(self):
        invalid = {
            "robustness_screen": {"pass": True},
            "scenarios": {
                name: {"status": "COMPLETED", "net_return_pct": 1.0,
                       "max_drawdown_pct": 3.0, "profit_factor": 1.3,
                       "equity_floor_hit": False}
                for name in ("base", "stress", "severe_stress")
            },
        }
        # A reported pass must not override a negative return or an equity-floor breach.
        invalid["scenarios"]["severe_stress"]["net_return_pct"] = -0.01
        summary = summarize_walk_forward_cost_stress([
            {"fold": 1, "trades": 40, "cost_stress": invalid},
        ])
        gate = summary["walk_forward_candidate_gate"]
        self.assertFalse(gate["pass"])
        self.assertEqual(summary["folds_passing_all_three_cost_scenarios"], 0)

        invalid["scenarios"]["severe_stress"]["net_return_pct"] = 0.1
        invalid["scenarios"]["stress"]["equity_floor_hit"] = True
        summary = summarize_walk_forward_cost_stress([
            {"fold": 1, "trades": 40, "cost_stress": invalid},
        ])
        self.assertFalse(summary["walk_forward_candidate_gate"]["pass"])

    def test_candidate_gate_rejects_malformed_equity_floor_flag(self):
        invalid = {
            "robustness_screen": {"pass": True},
            "scenarios": {
                name: {"status": "COMPLETED", "net_return_pct": 1.0,
                       "max_drawdown_pct": 3.0, "profit_factor": 1.3,
                       "equity_floor_hit": False}
                for name in ("base", "stress", "severe_stress")
            },
        }
        invalid["scenarios"]["stress"]["equity_floor_hit"] = "false"
        summary = summarize_walk_forward_cost_stress([
            {"fold": 1, "trades": 40, "cost_stress": invalid},
        ])
        gate = summary["walk_forward_candidate_gate"]
        self.assertFalse(gate["pass"])
        self.assertEqual(summary["folds_passing_all_three_cost_scenarios"], 0)

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


    def test_candidate_gate_rejects_boolean_trade_count(self):
        passing_stress = {
            "robustness_screen": {"pass": True},
            "scenarios": {
                name: {"status": "COMPLETED", "net_return_pct": 1.0,
                       "max_drawdown_pct": 3.0, "profit_factor": 1.3}
                for name in ("base", "stress", "severe_stress")
            },
        }
        summary = summarize_walk_forward_cost_stress([
            {"fold": 1, "trades": True, "cost_stress": passing_stress},
        ])
        gate = summary["walk_forward_candidate_gate"]
        self.assertFalse(gate["pass"])
        self.assertEqual(gate["under_sampled_folds"], [1])

    def test_candidate_gate_rejects_boolean_stress_metric(self):
        invalid = {
            "robustness_screen": {"pass": True},
            "scenarios": {
                name: {"status": "COMPLETED", "net_return_pct": 1.0,
                       "max_drawdown_pct": 3.0, "profit_factor": 1.3}
                for name in ("base", "stress", "severe_stress")
            },
        }
        invalid["scenarios"]["stress"]["profit_factor"] = True
        summary = summarize_walk_forward_cost_stress([
            {"fold": 1, "trades": 40, "cost_stress": invalid},
        ])
        self.assertFalse(summary["walk_forward_candidate_gate"]["pass"])
        self.assertEqual(summary["folds_passing_all_three_cost_scenarios"], 0)

    def test_oversized_integer_stress_metric_is_rejected_without_crash(self):
        invalid = {
            "robustness_screen": {"pass": True},
            "scenarios": {
                name: {"status": "COMPLETED", "net_return_pct": 1.0,
                       "max_drawdown_pct": 3.0, "profit_factor": 1.3}
                for name in ("base", "stress", "severe_stress")
            },
        }
        invalid["scenarios"]["stress"]["net_return_pct"] = 10 ** 1000
        summary = summarize_walk_forward_cost_stress([
            {"fold": 1, "trades": 40, "cost_stress": invalid},
        ])
        self.assertFalse(summary["walk_forward_candidate_gate"]["pass"])
        self.assertEqual(summary["folds_passing_all_three_cost_scenarios"], 0)



if __name__ == "__main__":
    unittest.main()


class FundingAuditPreservationIntegrationTests(unittest.TestCase):
    def test_report_flags_funding_events_outside_candle_range_before_simulation_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            candle_path = Path(tmp) / "candles.csv"
            funding_path = Path(tmp) / "funding.csv"
            output_path = Path(tmp) / "report.json"
            with candle_path.open("w", newline="", encoding="utf-8") as handle:
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
            with funding_path.open("w", newline="", encoding="utf-8") as handle:
                fields = ["funding_time", "funding_rate", "mark_price", "symbol",
                          "mark_price_source", "venue"]
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerow({
                    "funding_time": 0, "funding_rate": 0.0001, "mark_price": 100,
                    "symbol": "BTCUSDT", "mark_price_source": "test_fixture",
                    "venue": "binance_usdm",
                })
                writer.writerow({
                    "funding_time": 700_000_000, "funding_rate": 0.0001, "mark_price": 100,
                    "symbol": "BTCUSDT", "mark_price_source": "test_fixture",
                    "venue": "binance_usdm",
                })

            argv = [
                "run_backtest.py", "--csv", str(candle_path), "--symbol", "BTCUSDT",
                "--interval", "1h", "--train-fraction", "0.6", "--folds", "4",
                "--fast", "3", "--slow", "8", "--funding-csv", str(funding_path),
                "--output", str(output_path),
            ]
            with patch.object(sys, "argv", argv):
                main()

            report = json.loads(output_path.read_text(encoding="utf-8"))
            funding_quality = report["funding_data"]
            self.assertEqual(funding_quality["records_outside_candle_range"], 1)
            self.assertTrue(funding_quality["review_required"])
            self.assertEqual(report["funding_interpretation_gate"], "FUNDING_DATA_REVIEW_REQUIRED")


class CandleCsvSchemaValidationTests(unittest.TestCase):
    def _write_csv(self, path, headers, rows):
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers)
            writer.writeheader()
            writer.writerows(rows)

    def test_missing_required_column_has_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "candles.csv"
            self._write_csv(path, ["t", "o", "h", "l"], [])
            with self.assertRaisesRegex(ValueError, "missing required columns.*c"):
                load_csv(str(path), 3_600_000)

    def test_invalid_and_non_finite_values_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            for row, message in [
                ({"t": "0", "o": "bad", "h": "2", "l": "1", "c": "1.5"}, "invalid numeric"),
                ({"t": "0", "o": "1", "h": "inf", "l": "1", "c": "1.5"}, "non-finite"),
                ({"t": "0", "o": "1", "h": "2", "l": "1", "c": "1.5", "v": "-1"}, "negative volume"),
            ]:
                path = Path(tmp) / "candles.csv"
                self._write_csv(path, ["t", "o", "h", "l", "c", "v"], [row])
                with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                    load_csv(str(path), 3_600_000)

    def test_empty_csv_data_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "candles.csv"
            self._write_csv(path, ["t", "o", "h", "l", "c"], [])
            with self.assertRaisesRegex(ValueError, "no data rows"):
                load_csv(str(path), 3_600_000)


class FundingCsvMalformedRowTests(unittest.TestCase):
    def test_invalid_numeric_values_have_line_numbered_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "funding.csv"
            for row, expected in [
                ({"symbol": "BTCUSDT", "funding_time": "bad", "funding_rate": "0.001", "mark_price": "100"}, "invalid numeric"),
                ({"symbol": "BTCUSDT", "funding_time": "0", "funding_rate": "NaN", "mark_price": "100"}, "non-finite"),
                ({"symbol": "BTCUSDT", "funding_time": "0", "funding_rate": "0.001", "mark_price": "0"}, "invalid mark prices"),
            ]:
                with path.open("w", newline="", encoding="utf-8") as handle:
                    writer = csv.DictWriter(handle, fieldnames=["symbol", "funding_time", "funding_rate", "mark_price"])
                    writer.writeheader()
                    writer.writerow(row)
                with self.subTest(expected=expected):
                    if expected == "non-finite":
                        with self.assertRaisesRegex(ValueError, "non-finite rates"):
                            load_funding_csv(str(path))
                    elif expected == "invalid mark prices":
                        with self.assertRaisesRegex(ValueError, expected):
                            load_funding_csv(str(path))
                    else:
                        with self.assertRaisesRegex(ValueError, "line 2"):
                            load_funding_csv(str(path))

    def test_short_row_is_rejected_with_line_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "funding.csv"
            path.write_text("symbol,funding_time,funding_rate,mark_price\nBTCUSDT,0,0.001\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "malformed row at line 2"):
                load_funding_csv(str(path))
