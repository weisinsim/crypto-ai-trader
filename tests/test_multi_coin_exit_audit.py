import csv
import tempfile
import unittest
from unittest.mock import patch
import sys
from pathlib import Path

from validation.run_multi_coin_exit_audit import main, run_one


class MultiCoinExitAuditTests(unittest.TestCase):
    def test_cli_rejects_non_finite_costs(self):
        with patch.object(sys, "argv", ["run_multi_coin_exit_audit.py", "--cost-bps", "nan"]):
            with self.assertRaises(SystemExit) as raised:
                main()
        self.assertEqual(raised.exception.code, 2)

    def test_cli_rejects_duplicate_symbols(self):
        with patch.object(sys, "argv", ["run_multi_coin_exit_audit.py",
                                        "--symbols", "BTCUSDT", "btcusdt"]):
            with self.assertRaises(SystemExit) as raised:
                main()
        self.assertEqual(raised.exception.code, 2)

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
                             holdout_fraction=0.5, max_hold=2)

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


if __name__ == "__main__":
    unittest.main()
