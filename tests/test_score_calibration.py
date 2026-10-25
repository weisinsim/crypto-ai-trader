import unittest

from research.score_calibration import summarize_score_calibration


class ScoreCalibrationTests(unittest.TestCase):
    def test_groups_by_symbol_and_score_band_and_reports_net_metrics(self):
        trades = [
            {"symbol": "BTCUSDT", "score": 72, "net_return": 0.02, "exit_ts": 1},
            {"symbol": "BTCUSDT", "score": 79, "net_return": -0.01, "exit_ts": 2},
            {"symbol": "BTCUSDT", "score": 83, "net_return": 0.03, "exit_ts": 3},
            {"symbol": "ETHUSDT", "score": 95, "net_return": -0.02, "exit_ts": 4},
        ]
        report = summarize_score_calibration(trades, min_samples=2)
        self.assertEqual(report["status"], "RESEARCH_ONLY")
        self.assertFalse(report["auto_trade_authorized"])
        buckets = {(row["symbol"], row["score_band"]): row for row in report["buckets"]}
        btc_low = buckets[("BTCUSDT", "70-80")]
        self.assertEqual(btc_low["sample_count"], 2)
        self.assertEqual(btc_low["win_rate"], 0.5)
        self.assertAlmostEqual(btc_low["mean_net_expectancy"], 0.005)
        self.assertEqual(btc_low["validation_status"], "SAMPLE_THRESHOLD_MET")
        self.assertFalse(btc_low["score_is_probability"])
        self.assertEqual(buckets[("BTCUSDT", "80-90")]["sample_count"], 1)
        self.assertEqual(buckets[("ETHUSDT", "90-100")]["validation_status"], "INSUFFICIENT_SAMPLE")

    def test_metrics_follow_exit_time_order_for_drawdown(self):
        trades = [
            {"symbol": "SOLUSDT", "score": 91, "net_return": 0.10, "exit_ts": 2},
            {"symbol": "SOLUSDT", "score": 92, "net_return": -0.20, "exit_ts": 1},
        ]
        bucket = summarize_score_calibration(trades)["buckets"][0]
        self.assertAlmostEqual(bucket["max_drawdown"], 0.20)
        self.assertAlmostEqual(bucket["mean_net_expectancy"], -0.05)

    def test_same_exit_timestamp_drawdown_is_independent_of_input_order(self):
        trades = [
            {"signal_id": "b", "symbol": "BTCUSDT", "score": 92, "net_return": -0.20, "exit_ts": 10},
            {"signal_id": "a", "symbol": "BTCUSDT", "score": 92, "net_return": 0.10, "exit_ts": 10},
        ]
        forward = summarize_score_calibration(trades)["buckets"][0]
        reverse = summarize_score_calibration(list(reversed(trades)))["buckets"][0]
        self.assertEqual(forward["max_drawdown"], reverse["max_drawdown"])
        self.assertEqual(forward["mean_net_expectancy"], reverse["mean_net_expectancy"])

    def test_rejects_malformed_or_nonfinite_rows(self):
        invalid = [
            {"symbol": "BTCUSDT", "score": True, "net_return": 0.1, "exit_ts": 1},
            {"symbol": "BTCUSDT", "score": 101, "net_return": 0.1, "exit_ts": 1},
            {"symbol": "BTCUSDT", "score": 80, "net_return": float("nan"), "exit_ts": 1},
            {"symbol": "BTCUSDT", "score": 80, "net_return": -1, "exit_ts": 1},
            {"symbol": "", "score": 80, "net_return": 0.1, "exit_ts": 1},
        ]
        for row in invalid:
            with self.subTest(row=row), self.assertRaises(ValueError):
                summarize_score_calibration([row])

    def test_reports_trades_outside_configured_score_bands(self):
        trades = [
            {"symbol": "BTCUSDT", "score": 65, "net_return": 0.02, "exit_ts": 1},
            {"symbol": "BTCUSDT", "score": 75, "net_return": -0.01, "exit_ts": 2},
            {"symbol": "BTCUSDT", "score": 100, "net_return": 0.03, "exit_ts": 3},
        ]
        report = summarize_score_calibration(trades)
        self.assertEqual(report["input_trade_count"], 3)
        self.assertEqual(report["trade_count_in_bands"], 2)
        self.assertEqual(report["trade_count_outside_bands"], 1)

    def test_empty_input_does_not_fabricate_results(self):
        report = summarize_score_calibration([])
        self.assertEqual(report["trade_count_in_bands"], 0)
        self.assertEqual(report["buckets"], [])

    def test_optional_direction_split_prevents_long_short_mixing(self):
        trades = [
            {"symbol": "BTCUSDT", "side": "LONG", "score": 82, "net_return": 0.03, "exit_ts": 1},
            {"symbol": "BTCUSDT", "side": "SHORT", "score": 82, "net_return": -0.02, "exit_ts": 2},
        ]
        report = summarize_score_calibration(trades, min_samples=1, split_by_side=True)
        buckets = {(row["symbol"], row["score_band"], row["side"]): row for row in report["buckets"]}
        self.assertEqual(buckets[("BTCUSDT", "80-90", "LONG")]["mean_net_expectancy"], 0.03)
        self.assertEqual(buckets[("BTCUSDT", "80-90", "SHORT")]["mean_net_expectancy"], -0.02)
        self.assertTrue(report["split_by_side"])

    def test_rejects_overlapping_bands_and_missing_side_when_split(self):
        with self.assertRaisesRegex(ValueError, "must not overlap"):
            summarize_score_calibration([], bands=((70, 85), (80, 90)))
        row = {"symbol": "BTCUSDT", "score": 82, "net_return": 0.01, "exit_ts": 1}
        with self.assertRaisesRegex(ValueError, "side must be LONG or SHORT"):
            summarize_score_calibration([row], split_by_side=True)

    def test_score_100_is_in_top_band(self):
        report = summarize_score_calibration([
            {"symbol": "XRPUSDT", "score": 100, "net_return": 0.01, "exit_ts": 10}
        ])
        self.assertEqual(report["buckets"][0]["score_band"], "90-100")


if __name__ == "__main__":
    unittest.main()
