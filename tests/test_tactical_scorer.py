import unittest

from research.tactical_scorer import score_tactical_setup


class TacticalScorerTests(unittest.TestCase):
    def base(self, **overrides):
        args = {
            "symbol": "BTCUSDT",
            "model_status": "RESEARCH",
            "price": 101.0,
            "ema20_1h": 100.0,
            "ema20_4h": 99.0,
            "ema50_4h": 98.0,
            "rsi_1h": 50.0,
            "atr_1h": 2.0,
            "adx_1h": 22.0,
            "volume_ratio": 1.2,
            "previous_close": 100.0,
            "support": 98.0,
            "resistance": 105.0,
        }
        args.update(overrides)
        return score_tactical_setup(**args)

    def test_bullish_btc_candidate_has_deterministic_score_breakdown(self):
        result = self.base()
        self.assertEqual(result["signal"], "LONG")
        self.assertEqual(result["direction"], 1)
        self.assertEqual(result["score"], 90)
        self.assertEqual(result["score_breakdown"], {
            "trend": 30, "rsi": 20, "adx": 15, "volume": 15, "entry_location": 10,
        })
        self.assertEqual(result["entry"], 101.0)
        self.assertEqual(result["sl"], 98.6)
        self.assertEqual(result["tp1"], 104.0)
        self.assertEqual(result["tp2"], 105.5)

    def test_research_model_downgrades_low_score_candidate_to_watch(self):
        result = self.base(adx_1h=10, volume_ratio=0.5)
        self.assertEqual(result["signal"], "WATCH-LONG")
        self.assertLess(result["score"], 75)

    def test_asset_specific_stop_and_target_multipliers_are_preserved(self):
        sol = self.base(symbol="SOLUSDT")
        avax = self.base(symbol="AVAXUSDT")
        self.assertAlmostEqual(sol["sl"], 99.0)
        self.assertAlmostEqual(sol["tp1"], 105.0)
        self.assertAlmostEqual(avax["sl"], 98.6)
        self.assertAlmostEqual(avax["tp1"], 103.4)

    def test_bearish_setup_can_emit_short(self):
        result = self.base(
            price=97.0, ema20_1h=98.0, ema20_4h=99.0, ema50_4h=100.0,
            rsi_1h=50.0, previous_close=98.0, support=94.0, resistance=99.0,
        )
        self.assertEqual(result["signal"], "SHORT")
        self.assertEqual(result["direction"], -1)
        self.assertGreater(result["sl"], result["entry"])
        self.assertLess(result["tp1"], result["entry"])

    def test_rejects_unsupported_symbols_and_nonfinite_inputs(self):
        with self.assertRaisesRegex(ValueError, "supports"):
            self.base(symbol="XRPUSDT")
        with self.assertRaisesRegex(ValueError, "finite numeric"):
            self.base(volume_ratio=float("nan"))


if __name__ == "__main__":
    unittest.main()
