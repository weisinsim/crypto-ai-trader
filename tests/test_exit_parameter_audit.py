import unittest

from validation.exit_parameter_audit import evaluate, summarize, parse_ts


def candle(ts, open_, high, low, close):
    return {"ts": ts, "open": open_, "high": high, "low": low, "close": close}


class ExitAuditTests(unittest.TestCase):
    def test_epoch_milliseconds_are_normalized_to_seconds(self):
        self.assertEqual(parse_ts("1791600000000"), 1791600000)

    def test_target_hit_on_future_candle(self):
        candles = [
            candle(1, 100, 101, 99, 100),
            candle(2, 100, 106, 99, 104),
        ]
        signals = [{"ts": 1, "side": "LONG", "entry": 100, "atr": 1}]
        trades = evaluate(candles, signals, target_r=2.5, stop_atr=2,
                          cost_bps=0, max_hold=5)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["exit_reason"], "TP")
        self.assertAlmostEqual(trades[0]["net_R"], 2.5)

    def test_stop_first_when_stop_and_target_same_candle(self):
        candles = [
            candle(1, 100, 101, 99, 100),
            candle(2, 100, 106, 95, 102),
        ]
        signals = [{"ts": 1, "side": "LONG", "entry": 100, "atr": 1}]
        trades = evaluate(candles, signals, target_r=2.5, stop_atr=2,
                          cost_bps=0, max_hold=5)
        self.assertEqual(trades[0]["exit_reason"], "SL")
        self.assertEqual(trades[0]["net_R"], -1.0)

    def test_short_direction_stop_and_target(self):
        candles = [
            candle(1, 100, 101, 99, 100),
            candle(2, 100, 101, 94, 96),
        ]
        signals = [{"ts": 1, "side": "SHORT", "entry": 100, "atr": 1}]
        trades = evaluate(candles, signals, target_r=2.5, stop_atr=2,
                          cost_bps=0, max_hold=5)
        self.assertEqual(trades[0]["exit_reason"], "TP")
        self.assertAlmostEqual(trades[0]["net_R"], 2.5)

    def test_long_stop_gap_fills_at_worse_open(self):
        candles = [
            candle(1, 100, 101, 99, 100),
            candle(2, 95, 98, 94, 96),
        ]
        signals = [{"ts": 1, "side": "LONG", "entry": 100, "atr": 1}]
        trades = evaluate(candles, signals, target_r=2.5, stop_atr=2,
                          cost_bps=0, max_hold=1)
        self.assertEqual(trades[0]["exit_reason"], "SL")
        self.assertEqual(trades[0]["gross_R"], -5.0)

    def test_short_stop_gap_fills_at_worse_open(self):
        candles = [
            candle(1, 100, 101, 99, 100),
            candle(2, 105, 106, 102, 104),
        ]
        signals = [{"ts": 1, "side": "SHORT", "entry": 100, "atr": 1}]
        trades = evaluate(candles, signals, target_r=2.5, stop_atr=2,
                          cost_bps=0, max_hold=1)
        self.assertEqual(trades[0]["exit_reason"], "SL")
        self.assertEqual(trades[0]["gross_R"], -5.0)

    def test_round_trip_cost_is_subtracted_in_r(self):
        candles = [
            candle(1, 100, 101, 99, 100),
            candle(2, 100, 106, 99, 104),
        ]
        signals = [{"ts": 1, "side": "LONG", "entry": 100, "atr": 1}]
        trades = evaluate(candles, signals, target_r=2.5, stop_atr=2,
                          cost_bps=10, max_hold=5)
        self.assertAlmostEqual(trades[0]["cost_R"], 0.05)
        self.assertAlmostEqual(trades[0]["net_R"], 2.45)

    def test_unmatched_signal_is_not_counted(self):
        candles = [candle(1, 100, 101, 99, 100), candle(2, 100, 101, 99, 100)]
        signals = [{"ts": 9, "side": "LONG", "entry": 100, "atr": 1}]
        self.assertEqual(evaluate(candles, signals, 2.5, 2, 0, 5), [])

    def test_signal_specific_stop_overrides_fallback_atr(self):
        candles = [
            candle(1, 100, 101, 99, 100),
            candle(2, 100, 107, 99, 106),
        ]
        signals = [{"ts": 1, "side": "LONG", "entry": 100, "atr": 1, "stop": 98}]
        trades = evaluate(candles, signals, target_r=2.5, stop_atr=9,
                          cost_bps=0, max_hold=5)
        self.assertEqual(trades[0]["exit_reason"], "TP")
        self.assertAlmostEqual(trades[0]["net_R"], 2.5)

    def test_invalid_signal_stop_is_rejected(self):
        candles = [candle(1, 100, 101, 99, 100), candle(2, 100, 101, 99, 100)]
        signals = [{"ts": 1, "side": "LONG", "entry": 100, "atr": 1, "stop": 101}]
        with self.assertRaises(ValueError):
            evaluate(candles, signals, 2.5, 2, 0, 5)

    def test_development_trade_cannot_exit_using_holdout_candles(self):
        candles = [
            candle(1, 100, 101, 99, 100),
            candle(2, 100, 101, 99, 100),
            candle(3, 100, 110, 99, 109),
            candle(4, 100, 110, 99, 109),
        ]
        # The signal at ts=1 would hit 2.5R only on a later candle; if the
        # development slice ends at ts=2, its result must not use ts=3 or ts=4.
        dev_candles = candles[:2]
        signals = [{"ts": 1, "side": "LONG", "entry": 100, "atr": 1}]
        trades = evaluate(dev_candles, signals, target_r=2.5, stop_atr=2,
                          cost_bps=0, max_hold=72)
        # The split boundary must not shorten the holding window or borrow holdout candles.
        self.assertEqual(trades, [])

    def test_full_holding_window_without_hit_uses_time_exit(self):
        candles = [
            candle(1, 100, 101, 99, 100),
            candle(2, 100, 101, 99, 100),
            candle(3, 100, 101, 99, 101),
        ]
        signals = [{"ts": 1, "side": "LONG", "entry": 100, "atr": 1}]
        trades = evaluate(candles, signals, target_r=2.5, stop_atr=2,
                          cost_bps=0, max_hold=2)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["exit_reason"], "TIME")
        self.assertEqual(trades[0]["gross_R"], 1.0)

    def test_cli_fails_closed_on_missing_candle_interval(self):
        import subprocess
        import sys
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            candles = root / "candles.csv"
            signals = root / "signals.csv"
            out = root / "out.json"
            candles.write_text(
                "timestamp,open,high,low,close\n"
                "2025-01-01T00:59:59Z,100,101,99,100\n"
                "2025-01-01T01:59:59Z,100,101,99,100\n"
                "2025-01-01T03:59:59Z,100,101,99,100\n",
                encoding="utf-8",
            )
            signals.write_text(
                "timestamp,side,entry,atr\n"
                "2025-01-01T00:59:59Z,LONG,100,1\n",
                encoding="utf-8",
            )
            script = Path(__file__).resolve().parents[1] / "validation" / "exit_parameter_audit.py"
            run = subprocess.run([
                sys.executable, str(script), "--candles", str(candles),
                "--signals", str(signals), "--interval", "1h", "--out", str(out),
            ], capture_output=True, text=True)
            self.assertNotEqual(run.returncode, 0)
            self.assertIn("cadence validation failed", (run.stderr + run.stdout).lower())
            self.assertFalse(out.exists())

    def test_match_rate_threshold_is_enforced_by_cli(self):
        import subprocess
        import sys
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            candles = root / "candles.csv"
            signals = root / "signals.csv"
            out = root / "out.json"
            candles.write_text(
                "timestamp,open,high,low,close\n"
                "2025-01-01T00:59:59Z,100,101,99,100\n"
                "2025-01-01T01:59:59Z,100,101,99,100\n"
                "2025-01-01T02:59:59Z,100,101,99,100\n"
                "2025-01-01T03:59:59Z,100,101,99,100\n",
                encoding="utf-8",
            )
            signals.write_text(
                "timestamp,side,entry,atr\n"
                "2025-01-01T00:59:59Z,LONG,100,1\n"
                "2025-01-01T00:00:00Z,LONG,100,1\n",
                encoding="utf-8",
            )
            script = Path(__file__).resolve().parents[1] / "validation" / "exit_parameter_audit.py"
            run = subprocess.run([
                sys.executable, str(script), "--candles", str(candles),
                "--signals", str(signals), "--min-match-pct", "95",
                "--out", str(out),
            ], capture_output=True, text=True)
            self.assertNotEqual(run.returncode, 0)
            self.assertIn("No backtest was performed", run.stderr + run.stdout)

    def test_read_candles_rejects_non_finite_ohlc(self):
        import tempfile
        from pathlib import Path
        from validation.exit_parameter_audit import read_candles
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "candles.csv"
            path.write_text(
                "timestamp,open,high,low,close\n"
                "2025-01-01T00:59:59Z,NaN,101,99,100\n"
                "2025-01-01T01:59:59Z,100,101,99,100\n"
                "2025-01-01T02:59:59Z,100,101,99,100\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                read_candles(path)

    def test_read_signals_rejects_non_finite_parameters(self):
        import tempfile
        from pathlib import Path
        from validation.exit_parameter_audit import read_signals
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "signals.csv"
            path.write_text(
                "timestamp,side,entry,atr\n"
                "2025-01-01T00:59:59Z,LONG,100,NaN\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                read_signals(path)

    def test_single_position_filter_skips_overlapping_signals(self):
        from validation.exit_parameter_audit import filter_single_position
        trades = [
            {"timestamp": 100, "exit_timestamp": 300, "net_R": 1.0},
            {"timestamp": 200, "exit_timestamp": 400, "net_R": -1.0},
            {"timestamp": 300, "exit_timestamp": 500, "net_R": 0.5},
            {"timestamp": 500, "exit_timestamp": 600, "net_R": 0.2},
        ]
        accepted, skipped = filter_single_position(trades)
        self.assertEqual([t["timestamp"] for t in accepted], [100, 300, 500])
        self.assertEqual(skipped, 1)

    def test_summary_drawdown_uses_chronological_order(self):
        from validation.exit_parameter_audit import summarize
        trades = [
            {"timestamp": 300, "net_R": 2.0, "exit_reason": "TP"},
            {"timestamp": 100, "net_R": -1.0, "exit_reason": "SL"},
            {"timestamp": 200, "net_R": -1.0, "exit_reason": "SL"},
        ]
        stats = summarize(trades)
        self.assertEqual(stats["net_R"], 0.0)
        self.assertEqual(stats["max_drawdown_R"], 2.0)

    def test_summary_reports_overlapping_trades(self):
        stats = summarize([
            {"timestamp": 100, "exit_timestamp": 300, "net_R": 1.0, "exit_reason": "TP"},
            {"timestamp": 200, "exit_timestamp": 400, "net_R": -1.0, "exit_reason": "SL"},
            {"timestamp": 400, "exit_timestamp": 500, "net_R": 0.5, "exit_reason": "TIME"},
        ])
        self.assertEqual(stats["overlapping_trade_count"], 2)
        self.assertEqual(stats["max_concurrent_trades"], 2)

    def test_summary_counts_trades(self):
        stats = summarize([
            {"net_R": 2.0, "exit_reason": "TP"},
            {"net_R": -1.0, "exit_reason": "SL"},
        ])
        self.assertEqual(stats["trades"], 2)
        self.assertEqual(stats["win_rate_pct"], 50.0)
        self.assertEqual(stats["profit_factor"], 2.0)
        self.assertEqual(stats["net_R"], 1.0)


if __name__ == "__main__":
    unittest.main()
