import unittest

from research.v12_validation import run_v12_validation


def row(t, *, o=101.0, h=102.0, l=100.0, c=101.0):
    return {
        "t": t, "o": o, "h": h, "l": l, "c": c,
        "ema20_1h": 100.0, "ema50_1h": 99.0, "ema200_1h": 98.0,
        "ema20_4h": 100.0, "ema50_4h": 98.0, "ema200_4h": 97.0,
        "rsi_1h": 56.0, "atr_1h": 1.0, "atr_pct": 1.0, "adx_1h": 26.0,
        "vol_ratio": 1.5, "vwap_1h": 99.5, "support": 96.0, "resistance": 110.0,
        "funding_rate": -0.0005, "oi_change_pct": 4.0, "momentum_pct": 2.0,
        "macd_hist": 0.2, "breakout_up": True, "breakout_down": False,
        "retest_long": True, "retest_short": False, "cvd_direction": "UP",
        "market_regime": "BULL", "data_fresh": True, "candle_closed": True,
        "spread_bps": 3.0,
    }


class V12ValidationTests(unittest.TestCase):
    def test_validation_report_contains_holdout_folds_and_cost_stress(self):
        rows = [row(i * 3_600_000) for i in range(40)]
        report = run_v12_validation(rows, "BTCUSDT", holdout_fraction=0.2, folds=4)
        self.assertEqual(report["status"], "RESEARCH_ONLY")
        self.assertFalse(report["live_trading_enabled"])
        self.assertEqual(report["dataset"]["rows"], 40)
        self.assertEqual(len(report["walk_forward_folds"]), 4)
        self.assertIn("base", report["holdout_cost_stress"])
        self.assertIn("stress_1_5x", report["holdout_cost_stress"])
        self.assertIn("severe_2x", report["holdout_cost_stress"])
        self.assertEqual(report["threshold_calibration"]["training_rows"], 32)
        self.assertIn(report["threshold_calibration"]["status"],
                      ("TRAINING_CANDIDATE_SELECTED", "NO_CANDIDATE_PASSED_TRAINING_GATES"))
        self.assertIn("net_return_pct", report["calibrated_holdout"])
        self.assertIn(report["holdout_sample_gate"],
                      ("SUFFICIENT_FOR_SCREENING", "INSUFFICIENT_HOLDOUT_TRADES"))
        self.assertIn("net_return_pct", report["walk_forward_aggregate"])
        self.assertIn("training_calibration", report["walk_forward_folds"][0])
        self.assertIn("full_ensemble_fixed_threshold", report["component_ablation"]["results"])
        self.assertIn("without_momentum_funding", report["component_ablation"]["results"])

    def test_invalid_fold_count_rejected(self):
        with self.assertRaises(ValueError):
            run_v12_validation([row(i) for i in range(40)], "BTCUSDT", folds=1)


if __name__ == "__main__":
    unittest.main()
