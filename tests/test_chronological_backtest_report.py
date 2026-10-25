import unittest

from research.chronological_backtest_report import build_chronological_backtest_report


def row(trade_id, signal_ts, exit_ts, *, score=75, ret=0.02, status="SIMULATED",
        complete=True, reason="TP2", symbol="BTCUSDT", side="LONG"):
    return {
        "trade_id": trade_id, "signal_ts": signal_ts, "exit_ts": exit_ts,
        "symbol": symbol, "side": side, "score": score, "status": status,
        "outcome_complete": complete, "net_return": ret, "exit_reason": reason,
        "costs_verified": False, "fill_prices_verified": False,
    }


class ChronologicalBacktestReportTests(unittest.TestCase):
    def test_development_and_holdout_metrics_are_separate(self):
        report = build_chronological_backtest_report([
            row("dev", 10, 20, ret=-0.1),
            row("hold", 100, 110, ret=0.2),
            row("cross-exit", 90, 130, ret=0.05),
        ], cutoff_ts=100)
        self.assertEqual(report["development"]["completed_simulations"]["trade_count"], 2)
        self.assertEqual(report["holdout"]["completed_simulations"]["trade_count"], 1)
        self.assertAlmostEqual(report["development"]["completed_simulations"]["mean_net_return"], -0.025)
        self.assertAlmostEqual(report["holdout"]["completed_simulations"]["mean_net_return"], 0.2)
        self.assertTrue(report["cohorts_disjoint"])
        self.assertFalse(report["holdout_unseen_or_untuned_verified"])
        self.assertIsNone(report["verified_score_calibration"])

    def test_censored_holdout_trade_is_coverage_only(self):
        report = build_chronological_backtest_report([
            row("censored", 100, 120, status="CENSORED", complete=False, reason="DATA_END")
        ], cutoff_ts=100)
        self.assertEqual(report["holdout"]["right_censored_count"], 1)
        self.assertEqual(report["holdout"]["completed_simulation_count"], 0)

    def test_duplicate_id_rejected_before_reporting(self):
        item = row("same", 1, 2)
        with self.assertRaisesRegex(ValueError, "duplicate trade_id"):
            build_chronological_backtest_report([item, item], cutoff_ts=10)

    def test_invalid_timestamp_rejected(self):
        with self.assertRaisesRegex(ValueError, "exits before"):
            build_chronological_backtest_report([row("bad", 10, 9)], cutoff_ts=10)

    def test_empty_cohorts_safe(self):
        report = build_chronological_backtest_report([], cutoff_ts=10)
        self.assertEqual(report["development"]["completed_simulation_count"], 0)
        self.assertEqual(report["holdout"]["completed_simulation_count"], 0)


if __name__ == "__main__":
    unittest.main()
