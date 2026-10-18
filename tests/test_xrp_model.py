import unittest

from validation.xrp_model import evaluate_xrp_signal


PARAMS = {
    "kill_slope": 0.0077,
    "atr_max": 0.0135,
    "shock_max": 0.025,
    "sl_atr": 1.7,
    "tp_atr": 2.8,
}


class XRPSharedModelTests(unittest.TestCase):
    def test_watch_long_keeps_score_and_risk_levels(self):
        result = evaluate_xrp_signal(
            [99.8, 99.9, 100.0],
            e20_1h=99.7,
            e50_1h=99.5,
            e20_4h=100.5,
            e50_4h=100.0,
            rsi_1h=50.0,
            atr_1h=1.0,
            resistance=101.0,
            params=PARAMS,
        )
        self.assertEqual(result["signal"], "WATCH-LONG")
        self.assertEqual(result["direction"], 1)
        self.assertEqual(result["score"], 43)
        self.assertAlmostEqual(result["entry"], 100.0)
        self.assertAlmostEqual(result["stop"], 98.3)
        self.assertAlmostEqual(result["target1"], 102.8)
        self.assertAlmostEqual(result["target2"], 103.5)
        self.assertFalse(result["kill"])

    def test_pullback_entry_uses_existing_long_rule(self):
        result = evaluate_xrp_signal(
            [99.0, 99.5, 99.4, 100.0],
            e20_1h=99.8,
            e50_1h=99.6,
            e20_4h=100.5,
            e50_4h=100.0,
            rsi_1h=50.0,
            atr_1h=1.0,
            resistance=101.0,
            params=PARAMS,
        )
        self.assertEqual(result["signal"], "LONG")
        self.assertEqual(result["score"], 75)
        self.assertEqual(result["direction"], 1)

    def test_shock_kill_switch_suppresses_signal_and_risk_levels(self):
        result = evaluate_xrp_signal(
            [99.0, 100.0, 103.0],
            e20_1h=100.0,
            e50_1h=99.5,
            e20_4h=100.5,
            e50_4h=100.0,
            rsi_1h=50.0,
            atr_1h=1.0,
            resistance=101.0,
            params=PARAMS,
        )
        self.assertTrue(result["kill"])
        self.assertEqual(result["signal"], "NO-TRADE")
        self.assertEqual(result["score"], 0)
        self.assertEqual(result["direction"], 0)
        self.assertIsNone(result["entry"])
        self.assertIsNone(result["stop"])
        self.assertIsNone(result["target1"])
        self.assertIsNone(result["target2"])

    def test_zero_atr_uses_live_fallback(self):
        result = evaluate_xrp_signal(
            [99.8, 99.9, 100.0],
            e20_1h=99.7,
            e50_1h=99.5,
            e20_4h=100.5,
            e50_4h=100.0,
            rsi_1h=50.0,
            atr_1h=0.0,
            resistance=101.0,
            params=PARAMS,
        )
        self.assertEqual(result["signal"], "WATCH-LONG")
        self.assertAlmostEqual(result["stop"], 98.3)
        self.assertAlmostEqual(result["target1"], 102.8)
        self.assertAlmostEqual(result["target2"], 103.5)


if __name__ == "__main__":
    unittest.main()
