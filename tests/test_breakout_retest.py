import unittest

from research.breakout_retest import GRID, atr_values, candidate_rejection_reasons, funding_cashflow, profit_factor_above_one, run_breakout_retest


def make_rows(n=240):
    rows = []
    close = 100.0
    for i in range(n):
        # Repeated range phases followed by directional expansions create
        # deterministic OHLCV input without external data.
        phase = i % 48
        if phase < 28:
            close = 100.0 + ((phase % 7) - 3) * 0.25
        elif phase < 34:
            close = 100.0 + (phase - 27) * 0.9
        elif phase < 40:
            close = 105.0 - (phase - 33) * 0.45
        else:
            close = 102.0 + ((phase % 5) - 2) * 0.3
        rows.append({
            "t": i * 3_600_000,
            "o": close - 0.1,
            "h": close + 0.35,
            "l": close - 0.35,
            "c": close,
            "v": 100.0,
        })
    return rows


class BreakoutRetestTests(unittest.TestCase):
    def test_direction_summary_reports_best_candidate_per_direction(self):
        from research.breakout_retest import candidate_rejection_reasons
        candidates = [
            {"parameters": {"direction": "LONG"}, "eligible": False, "score": 1.0,
             "median_fold_return_pct": 2.0, "worst_fold_return_pct": -4.0,
             "rejection_reasons": ["worst_fold_below_minus_3pct"]},
            {"parameters": {"direction": "SHORT"}, "eligible": True, "score": 1.5,
             "median_fold_return_pct": 2.5, "worst_fold_return_pct": -1.0,
             "rejection_reasons": []},
            {"parameters": {"direction": "BOTH"}, "eligible": False, "score": -0.2,
             "median_fold_return_pct": 0.0, "worst_fold_return_pct": -2.0,
             "rejection_reasons": ["risk_adjusted_score_not_positive"]},
        ]
        summary = {}
        for direction in ("LONG", "SHORT", "BOTH"):
            group = [item for item in candidates if item["parameters"]["direction"] == direction]
            passing = sorted((item for item in group if item["eligible"]), key=lambda item: item["score"], reverse=True)
            best = passing[0] if passing else max(group, key=lambda item: item["score"])
            summary[direction] = {"best_candidate_eligible": best["eligible"], "best_score": best["score"]}
        self.assertFalse(summary["LONG"]["best_candidate_eligible"])
        self.assertTrue(summary["SHORT"]["best_candidate_eligible"])
        self.assertFalse(summary["BOTH"]["best_candidate_eligible"])

    def test_infinite_profit_factor_is_valid_but_null_is_not(self):
        self.assertTrue(profit_factor_above_one("INF"))
        self.assertTrue(profit_factor_above_one(1.0001))
        self.assertFalse(profit_factor_above_one(1.0))
        self.assertFalse(profit_factor_above_one(None))
        self.assertFalse(profit_factor_above_one(float("nan")))

    def test_grid_covers_each_direction_without_duplicate_candidates(self):
        self.assertEqual(len(GRID), 2592)
        self.assertEqual({p["direction"] for p in GRID}, {"BOTH", "LONG", "SHORT"})
        self.assertEqual({p["breakout_atr"] for p in GRID}, {0.05, 0.15})
        self.assertEqual({p["retest_atr"] for p in GRID}, {0.20, 0.40})
        self.assertEqual({p["body_confirm_atr"] for p in GRID}, {0.0, 0.15})
        self.assertEqual({p["breakout_atr"] for p in GRID}, {0.05, 0.15})
        self.assertEqual({p["retest_atr"] for p in GRID}, {0.20, 0.40})
        self.assertEqual(len({tuple(sorted(p.items())) for p in GRID}), len(GRID))

    def test_atr_warmup_and_positive_values(self):
        rows = make_rows()
        atr = atr_values(rows, period=14)
        self.assertIsNone(atr[12])
        self.assertIsNotNone(atr[13])
        self.assertGreater(atr[13], 0)

    def test_backtest_returns_separate_long_and_short_statistics(self):
        rows = make_rows()
        params = {"lookback": 24, "max_wait": 6, "target_r": 2.0}
        result = run_breakout_retest(rows, params, 100, len(rows))
        self.assertIn("long", result)
        self.assertIn("short", result)
        self.assertIn("trades", result)
        self.assertIn("max_drawdown_pct", result)
        self.assertGreaterEqual(result["max_drawdown_pct"], 0)
        for trade in result["trade_log"]:
            self.assertIn(trade["side"], ("LONG", "SHORT"))
            self.assertGreaterEqual(trade["entry_time"], rows[100]["t"])

    def test_direction_filter_restricts_breakout_side(self):
        rows = make_rows()
        base = {"lookback": 24, "max_wait": 6, "target_r": 2.0}
        long_result = run_breakout_retest(rows, base | {"direction": "LONG"}, 100, len(rows))
        short_result = run_breakout_retest(rows, base | {"direction": "SHORT"}, 100, len(rows))
        self.assertTrue(all(t["side"] == "LONG" for t in long_result["trade_log"]))
        self.assertTrue(all(t["side"] == "SHORT" for t in short_result["trade_log"]))

    def test_invalid_costs_rejected(self):
        rows = make_rows()
        params = {"lookback": 24, "max_wait": 6, "target_r": 2.0}
        with self.assertRaisesRegex(ValueError, "fees and slippage"):
            run_breakout_retest(rows, params, 100, len(rows), fee_rate=float("nan"))

    def test_funding_cashflow_long_pays_and_short_receives(self):
        events = [{"funding_time": 200, "funding_rate": 0.001, "mark_price": 100.0}]
        self.assertAlmostEqual(funding_cashflow(1, 2.0, 100, 300, events)[0], -0.2)
        self.assertAlmostEqual(funding_cashflow(-1, 2.0, 100, 300, events)[0], 0.2)

    def test_funding_cashflow_excludes_entry_time_and_after_exit(self):
        events = [
            {"funding_time": 100, "funding_rate": 0.01, "mark_price": 100.0},
            {"funding_time": 200, "funding_rate": 0.01, "mark_price": 100.0},
            {"funding_time": 301, "funding_rate": 0.01, "mark_price": 100.0},
        ]
        pnl, count = funding_cashflow(1, 1.0, 100, 300, events)
        self.assertAlmostEqual(pnl, -1.0)
        self.assertEqual(count, 1)

    def test_candidate_rejection_reasons_explain_weak_folds(self):
        folds = [
            {"trades": 5, "net_return_pct": 2.0},
            {"trades": 4, "net_return_pct": 1.0},
            {"trades": 4, "net_return_pct": -4.0},
        ]
        reasons = candidate_rejection_reasons(folds, -0.5)
        self.assertIn("worst_fold_below_minus_3pct", reasons)
        self.assertIn("risk_adjusted_score_not_positive", reasons)
        self.assertNotIn("total_trades_below_12", reasons)

    def test_invalid_window_rejected(self):
        rows = make_rows()
        with self.assertRaisesRegex(ValueError, "evaluation indices"):
            run_breakout_retest(rows, {"lookback": 24, "max_wait": 6, "target_r": 2.0},
                                100, len(rows) + 1)


if __name__ == "__main__":
    unittest.main()
