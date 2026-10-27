import unittest

from research.paper_backtest_report import build_paper_backtest_report


def trade(trade_id, *, symbol="BTCUSDT", side="LONG", score=75, ret=0.02,
          status="SIMULATED", complete=True, exit_ts=1000,
          reason="TP2", costs=False, fills=False):
    return {
        "trade_id": trade_id, "symbol": symbol, "side": side, "score": score,
        "status": status, "outcome_complete": complete, "net_return": ret,
        "exit_timestamp_ms": exit_ts, "exit_reason": reason,
        "costs_verified": costs, "fill_prices_verified": fills,
    }


class PaperBacktestReportTests(unittest.TestCase):
    def test_censored_rows_excluded_and_grouped(self):
        rows = [
            trade("a", ret=0.1, exit_ts=2000),
            trade("b", symbol="SOLUSDT", side="SHORT", score=85, ret=-0.05, exit_ts=3000),
            trade("c", status="CENSORED", complete=False, reason="DATA_END", exit_ts=4000),
        ]
        report = build_paper_backtest_report(rows)
        self.assertEqual(report["completed_simulation_count"], 2)
        self.assertEqual(report["right_censored_count"], 1)
        self.assertEqual(report["completed_simulations"]["trade_count"], 2)
        self.assertEqual(report["censored_trade_ids"], ["c"])
        self.assertEqual(len(report["by_symbol_side"]), 2)
        self.assertFalse(report["costs_verified"])
        self.assertFalse(report["fills_verified"])
        self.assertIsNone(report["verified_score_calibration"])
        self.assertFalse(report["score_is_probability"])

    def test_empty_report_is_safe(self):
        report = build_paper_backtest_report([])
        self.assertEqual(report["completed_simulations"]["trade_count"], 0)
        self.assertIsNone(report["completed_simulations"]["win_rate"])
        self.assertEqual(report["by_score_band"], [])
        self.assertFalse(report["auto_trade_authorized"])

    def test_duplicate_ids_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate trade_id"):
            build_paper_backtest_report([trade("dup"), trade("dup")])

    def test_invalid_returns_rejected(self):
        with self.assertRaisesRegex(ValueError, "net_return"):
            build_paper_backtest_report([trade("bad", ret=-1.0)])

    def test_invalid_score_rejected(self):
        with self.assertRaisesRegex(ValueError, "score"):
            build_paper_backtest_report([trade("bad", score=101)])

    def test_incomplete_non_censored_row_is_rejected_not_censored(self):
        report = build_paper_backtest_report([
            trade("partial", status="NO_TRADE", complete=False, reason="SIGNAL_REJECTED")
        ])
        self.assertEqual(report["right_censored_count"], 0)
        self.assertEqual(report["rejected_count"], 1)
        self.assertEqual(report["rejected"][0]["reason"], "NOT_A_COMPLETED_SIMULATION")

    def test_censored_row_cannot_claim_complete(self):
        with self.assertRaisesRegex(ValueError, "censored but marked"):
            build_paper_backtest_report([
                trade("contradiction", status="CENSORED", complete=True, reason="DATA_END")
            ])

    def test_score_band_100_is_included(self):
        report = build_paper_backtest_report([trade("max", score=100)])
        self.assertEqual(report["by_score_band"][0]["score_band"], "90-100")
        self.assertEqual(report["by_score_band"][0]["trade_count"], 1)

    def test_noncompleted_status_not_counted_as_completed(self):
        report = build_paper_backtest_report([
            trade("unknown", status="NO_TRADE", complete=True)
        ])
        self.assertEqual(report["completed_simulation_count"], 0)
        self.assertEqual(report["rejected_count"], 1)


if __name__ == "__main__":
    unittest.main()
