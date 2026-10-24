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


if __name__ == "__main__":
    unittest.main()
