import unittest

from research.v12_hybrid_engine import MODEL_VERSION, evaluate_hybrid


def strong_long_features():
    return {
        "price": 101.0, "ema20_1h": 100.0, "ema50_1h": 99.0, "ema200_1h": 98.0,
        "ema20_4h": 100.0, "ema50_4h": 98.0, "ema200_4h": 97.0,
        "rsi_1h": 56.0, "atr_1h": 1.0, "atr_pct": 1.0, "adx_1h": 26.0,
        "vol_ratio": 1.5, "vwap_1h": 99.5, "support": 96.0, "resistance": 110.0,
        "funding_rate": -0.0005, "oi_change_pct": 4.0, "momentum_pct": 2.0,
        "breakout_up": True, "breakout_down": False, "retest_long": True, "retest_short": False,
        "cvd_direction": "UP", "market_regime": "BULL",
        "data_fresh": True, "candle_closed": True, "spread_bps": 3.0,
    }

def strong_short_features():
    return {
        "price": 99.0, "ema20_1h": 100.0, "ema50_1h": 101.0, "ema200_1h": 102.0,
        "ema20_4h": 100.0, "ema50_4h": 102.0, "ema200_4h": 103.0,
        "rsi_1h": 25.0, "atr_1h": 1.0, "atr_pct": 1.0, "adx_1h": 26.0,
        "vol_ratio": 1.5, "vwap_1h": 100.0, "support": 90.0, "resistance": 103.0,
        "funding_rate": 0.0005, "oi_change_pct": 4.0, "momentum_pct": -2.0,
        "breakout_up": False, "breakout_down": True, "retest_long": False, "retest_short": True,
        "cvd_direction": "DOWN", "market_regime": "BEAR",
        "data_fresh": True, "candle_closed": True, "spread_bps": 3.0,
    }


class V12HybridEngineTests(unittest.TestCase):
    def test_missing_required_features_fail_closed(self):
        result = evaluate_hybrid("BTCUSDT", {"price": 100})
        self.assertEqual(result["signal"], "NO-TRADE")
        self.assertEqual(result["status"], "INSUFFICIENT_DATA")
        self.assertTrue(result["missing"])

    def test_open_candle_fails_closed(self):
        features = strong_long_features()
        features["candle_closed"] = False
        result = evaluate_hybrid("BTCUSDT", features)
        self.assertEqual(result["signal"], "NO-TRADE")
        self.assertEqual(result["status"], "STALE_OR_OPEN_CANDLE")

    def test_hybrid_returns_auditable_candidate(self):
        result = evaluate_hybrid("BTCUSDT", strong_long_features())
        self.assertEqual(result["model_version"], MODEL_VERSION)
        self.assertEqual(result["signal"], "LONG")
        self.assertEqual(result["status"], "RESEARCH_CANDIDATE")
        self.assertIn("momentum_funding", result["components"]["long"])
        self.assertIn("trend_breakout", result["components"]["long"])
        self.assertIn("confluence", result["components"]["long"])
        self.assertIn("asset_adaptive", result["components"]["long"])

    def test_nearby_resistance_blocks_long(self):
        features = strong_long_features()
        features["resistance"] = 101.2
        result = evaluate_hybrid("BTCUSDT", features)
        self.assertNotEqual(result["signal"], "LONG")
        self.assertTrue(any("resistance" in reason.lower() for reason in result["reasons"]))

    def test_frozen_xrp_model_is_not_overridden(self):
        result = evaluate_hybrid("XRPUSDT", strong_long_features())
        self.assertEqual(result["signal"], "NO-TRADE")
        self.assertEqual(result["status"], "EXTERNAL_FROZEN_MODEL_REQUIRED")

    def test_invalid_threshold_override_fails_closed(self):
        result = evaluate_hybrid("BTCUSDT", strong_long_features(), min_score_override=10)
        self.assertEqual(result["signal"], "NO-TRADE")
        self.assertEqual(result["status"], "INVALID_THRESHOLD_OVERRIDE")

    def test_short_rsi_exhaustion_filter_blocks_oversold_short(self):
        baseline = evaluate_hybrid("BTCUSDT", strong_short_features())
        filtered = evaluate_hybrid("BTCUSDT", strong_short_features(), short_rsi_floor=30)
        self.assertEqual(baseline["signal"], "SHORT")
        self.assertEqual(filtered["signal"], "NO-TRADE")
        self.assertEqual(filtered["status"], "SHORT_EXHAUSTION_FILTER")

    def test_component_weight_override_is_normalized(self):
        result = evaluate_hybrid(
            "BTCUSDT", strong_long_features(),
            component_weights_override={"momentum_funding": 0.0},
        )
        weights = result["components"]["weights"]
        self.assertEqual(weights["momentum_funding"], 0.0)
        self.assertAlmostEqual(sum(weights.values()), 1.0)

    def test_all_zero_component_weights_fail_closed(self):
        result = evaluate_hybrid(
            "BTCUSDT", strong_long_features(),
            component_weights_override={
                "momentum_funding": 0.0, "trend_breakout": 0.0,
                "confluence": 0.0, "asset_adaptive": 0.0,
            },
        )
        self.assertEqual(result["signal"], "NO-TRADE")
        self.assertEqual(result["status"], "INVALID_COMPONENT_WEIGHTS")


if __name__ == "__main__":
    unittest.main()
