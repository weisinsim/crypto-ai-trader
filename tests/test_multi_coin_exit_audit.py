import csv
import tempfile
import unittest
from unittest.mock import patch
import sys
from pathlib import Path

from validation.run_multi_coin_exit_audit import aggregate_holdout_sample_gate, main, run_one


class MultiCoinExitAuditTests(unittest.TestCase):

    def test_aggregate_sample_gate_fails_closed_for_empty_reports(self):
        gate = aggregate_holdout_sample_gate([])
        self.assertEqual(gate["coins_checked"], 0)
        self.assertEqual(gate["scenarios_checked"], 0)
        self.assertFalse(gate["all_coins_meet_minimum"])

    def test_aggregate_sample_gate_reports_failing_symbols(self):
        reports = [
            {"symbol": "BTCUSDT", "holdout_sample_gate": {
                "scenarios_checked": 8, "scenarios_below_minimum": 0,
                "all_scenarios_meet_minimum": True}},
            {"symbol": "SOLUSDT", "holdout_sample_gate": {
                "scenarios_checked": 8, "scenarios_below_minimum": 3,
                "all_scenarios_meet_minimum": False}},
            {"symbol": "ETHUSDT", "holdout_sample_gate": {
                "scenarios_checked": 0, "scenarios_below_minimum": 0,
                "all_scenarios_meet_minimum": False}},
        ]
        gate = aggregate_holdout_sample_gate(reports)
        self.assertEqual(gate["coins_checked"], 3)
        self.assertEqual(gate["scenarios_checked"], 16)
        self.assertEqual(gate["scenarios_below_minimum"], 3)
        self.assertEqual(gate["coins_failing_sample_gate"], ["SOLUSDT", "ETHUSDT"])
        self.assertFalse(gate["all_coins_meet_minimum"])
    def test_cli_rejects_non_finite_costs(self):
        with patch.object(sys, "argv", ["run_multi_coin_exit_audit.py", "--cost-bps", "nan"]):
            with self.assertRaises(SystemExit) as raised:
                main()
        self.assertEqual(raised.exception.code, 2)

    def test_cli_rejects_non_positive_minimum_holdout_trades(self):
        with patch.object(sys, "argv", ["run_multi_coin_exit_audit.py",
                                        "--min-holdout-trades", "0"]):
            with self.assertRaises(SystemExit) as raised:
                main()
        self.assertEqual(raised.exception.code, 2)


    def test_cli_rejects_duplicate_cost_scenarios(self):
        with patch.object(sys, "argv", ["run_multi_coin_exit_audit.py",
                                        "--cost-bps", "5", "5"]):
            with self.assertRaises(SystemExit) as raised:
                main()
        self.assertEqual(raised.exception.code, 2)

    def test_cli_rejects_duplicate_symbols(self):
        with patch.object(sys, "argv", ["run_multi_coin_exit_audit.py",
                                        "--symbols", "BTCUSDT", "btcusdt"]):
            with self.assertRaises(SystemExit) as raised:
                main()
        self.assertEqual(raised.exception.code, 2)



    def test_run_one_rejects_duplicate_costs_for_direct_callers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir, signal_dir = root / "data", root / "signals"
            data_dir.mkdir()
            signal_dir.mkdir()
            with self.assertRaisesRegex(ValueError, "Duplicate cost scenarios"):
                run_one("TESTUSDT", data_dir, signal_dir, [5, 5],
                        holdout_fraction=0.3, max_hold=72)

    def test_run_one_rejects_invalid_minimum_sample_threshold(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir, signal_dir = root / "data", root / "signals"
            data_dir.mkdir()
            signal_dir.mkdir()
            with self.assertRaisesRegex(ValueError, "positive integers"):
                run_one("TESTUSDT", data_dir, signal_dir, [5],
                        holdout_fraction=0.3, max_hold=72, min_holdout_trades=True)

    def test_sample_gate_requires_all_cost_target_scenarios(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir, signal_dir = root / "data", root / "signals"
            data_dir.mkdir()
            signal_dir.mkdir()
            timestamps = [
                "2026-02-01T00:59:59Z", "2026-02-01T01:59:59Z",
                "2026-02-01T02:59:59Z", "2026-02-01T03:59:59Z",
                "2026-02-01T04:59:59Z", "2026-02-01T05:59:59Z",
                "2026-02-01T06:59:59Z", "2026-02-01T07:59:59Z",
            ]
            with (data_dir / "TESTUSDT_1h.csv").open("w", newline="", encoding="utf-8") as stream:
                writer = csv.writer(stream)
                writer.writerow(["timestamp", "open", "high", "low", "close"])
                for ts in timestamps:
                    writer.writerow([ts, 100, 101, 99, 100])
            with (signal_dir / "TESTUSDT_signals.csv").open("w", newline="", encoding="utf-8") as stream:
                writer = csv.writer(stream)
                writer.writerow(["timestamp", "side", "entry", "atr", "stop"])
                # Only one holdout signal; both target scenarios have a valid full
                # replay window but cannot meet the deliberately strict threshold.
                writer.writerow([timestamps[6], "LONG", 100, 1, 98])
            result = run_one("TESTUSDT", data_dir, signal_dir, [0, 10],
                             holdout_fraction=0.25, max_hold=1, min_holdout_trades=2)
            gate = result["holdout_sample_gate"]
            self.assertEqual(gate["scenarios_checked"], 4)
            self.assertEqual(gate["scenarios_below_minimum"], 4)
            self.assertFalse(gate["all_scenarios_meet_minimum"])
            self.assertFalse(aggregate_holdout_sample_gate([result])["all_coins_meet_minimum"])

    def test_development_signals_cannot_exit_on_holdout_candles(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            signal_dir = root / "signals"
            data_dir.mkdir()
            signal_dir.mkdir()
            candle_path = data_dir / "TESTUSDT_1h.csv"
            signal_path = signal_dir / "TESTUSDT_signals.csv"

            timestamps = [
                "2026-01-01T00:59:59Z",
                "2026-01-01T01:59:59Z",
                "2026-01-01T02:59:59Z",
                "2026-01-01T03:59:59Z",
                "2026-01-01T04:59:59Z",
                "2026-01-01T05:59:59Z",
            ]
            candles = [
                (100, 101, 99, 100),
                (100, 101, 99, 100),
                (100, 101, 99, 100),
                (100, 101, 99, 100),
                (100, 106, 99, 104),
                (104, 105, 103, 104),
            ]
            with candle_path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.writer(stream)
                writer.writerow(["timestamp", "open", "high", "low", "close"])
                for ts, (open_, high, low, close) in zip(timestamps, candles):
                    writer.writerow([ts, open_, high, low, close])

            with signal_path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.writer(stream)
                writer.writerow(["timestamp", "side", "entry", "atr", "stop"])
                # The development signal is on the last development candle.
                # It can only reach its target on the first holdout candle.
                writer.writerow([timestamps[2], "LONG", 100, 1, 98])
                writer.writerow([timestamps[3], "LONG", 100, 1, 98])
                # A signal timestamp absent from the candle file must be surfaced,
                # not silently treated as valid evidence.
                writer.writerow(["2026-01-02T00:00:00Z", "LONG", 100, 1, 98])

            result = run_one("TESTUSDT", data_dir, signal_dir, [0],
                             holdout_fraction=0.45, max_hold=2, min_holdout_trades=2)

            development = result["periods"]["development"]["cost_0_bps"]["target_2.5R"]
            holdout = result["periods"]["holdout"]["cost_0_bps"]["target_2.5R"]
            self.assertEqual(development["trades"], 0)
            self.assertEqual(holdout["trades"], 1)
            self.assertEqual(result["periods"]["development"]["candle_rows"], 3)
            self.assertEqual(result["periods"]["holdout"]["candle_rows"], 3)
            self.assertEqual(result["matched_signals"], 2)
            self.assertEqual(result["unmatched_signals"], 1)
            self.assertEqual(result["signal_match_rate"], 0.66666667)
            self.assertEqual(result["evidence_quality_status"], "BLOCKED_UNMATCHED_SIGNALS")
            self.assertEqual(result["holdout_sample_gate"]["scenarios_checked"], 2)
            self.assertEqual(result["holdout_sample_gate"]["minimum_trades_required_per_scenario"], 2)
            self.assertFalse(result["holdout_sample_gate"]["all_scenarios_meet_minimum"])
            self.assertGreater(result["holdout_sample_gate"]["scenarios_below_minimum"], 0)
            self.assertEqual(
                result["periods"]["holdout"]["cost_0_bps"]["target_2.5R"]["sample_status"],
                "INSUFFICIENT_SAMPLE",
            )


if __name__ == "__main__":
    unittest.main()
