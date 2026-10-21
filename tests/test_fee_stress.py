"""Tests for fee-rate sensitivity analysis."""
import unittest

from execution.fee_stress import run_fee_stress


class FeeStressTests(unittest.TestCase):
    def setUp(self):
        self.scenarios = [{
            "event": {
                "event_id": "fee-1", "symbol": "BTCUSDT", "side": "LONG",
                "eligible": True, "notification_only": True,
                "auto_trade_authorized": False, "entry_price": 100.0,
                "stop_loss": 95.0, "take_profit_1": 105.0,
                "take_profit_2": 110.0,
            },
            "candles": [{"timestamp_ms": 2000, "open": 100, "high": 111,
                         "low": 99, "close": 110}],
            "quantity": 2,
            "opened_at_ms": 1000,
        }]

    def test_higher_fees_reduce_net_pnl(self):
        result = run_fee_stress(
            self.scenarios, fee_rates=(0, 0.0005, 0.001), starting_equity=100
        )
        cases = result["cases"]
        self.assertEqual([c["fee_rate_bps"] for c in cases], [0, 5, 10])
        self.assertGreater(cases[0]["net_pnl"], cases[1]["net_pnl"])
        self.assertGreater(cases[1]["net_pnl"], cases[2]["net_pnl"])
        self.assertEqual(result["orders_sent"], 0)
        self.assertFalse(result["slippage_modeled"])

    def test_rejects_invalid_fee_rates(self):
        for rates in ((), (0.001, 0.001), (-0.1,), (0.1,), (float("nan"),)):
            with self.subTest(rates=rates):
                with self.assertRaises(ValueError):
                    run_fee_stress(self.scenarios, fee_rates=rates)

    def test_empty_scenarios_is_valid(self):
        result = run_fee_stress([], fee_rates=(0.0005,))
        self.assertEqual(result["scenario_count"], 0)
        self.assertEqual(result["cases"][0]["trade_count"], 0)


if __name__ == "__main__":
    unittest.main()
