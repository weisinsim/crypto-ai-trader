import unittest
from unittest.mock import patch

from research.cost_stress import run_cost_stress_scenarios
from research.backtest import run_ema_cross_backtest


class CostStressTests(unittest.TestCase):
    def _fake_backtest(self, candles, **kwargs):
        fee = kwargs["fee_rate"]
        slippage = kwargs["slippage_rate"]
        self.calls.append((candles, kwargs))
        return {
            "status": "OK",
            "trade_log": [{"funding_cashflow": 0.01, "funding_settlement_count": 2}],
            "net_return_pct": 2 - fee * 1000 - slippage * 1000,
            "max_drawdown_pct": 4 + fee * 1000 + slippage * 1000,
            "profit_factor": 1.2,
            "equity_final": 1.02,
            "equity_floor_hit": False,
        }

    def setUp(self):
        self.calls = []
        self.patch = patch("research.cost_stress.run_ema_cross_backtest", side_effect=self._fake_backtest)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_runs_three_full_scenarios_and_changes_only_cost_rates(self):
        candles = [{"t": 100}, {"t": 200}]
        funding = [{"funding_time": 100, "funding_rate": 0.001, "mark_price": 100}]
        report = run_cost_stress_scenarios(
            candles, base_fee_rate=0.001, base_slippage_rate=0.0005,
            backtest_kwargs={"fast": 5, "slow": 10, "funding_rates": funding},
            trade_start_ts=200,
        )
        self.assertEqual(report["status"], "FULL_BACKTEST_RERUNS")
        self.assertEqual(list(report["scenarios"]), ["base", "stress", "severe_stress"])
        self.assertEqual(len(self.calls), 3)
        self.assertEqual([call[1]["fee_rate"] for call in self.calls], [0.001, 0.0015, 0.002])
        self.assertEqual([call[1]["slippage_rate"] for call in self.calls], [0.0005, 0.00075, 0.001])
        self.assertTrue(all(call[1]["trade_start_ts"] == 200 for call in self.calls))
        self.assertTrue(all(call[1]["funding_rates"] == funding for call in self.calls))
        self.assertEqual(report["scenarios"]["base"]["funding_settlement_count_total"], 2)
        self.assertEqual(report["scenarios"]["severe_stress"]["fee_rate"], 0.002)

    def test_rejects_invalid_multipliers(self):
        for stress, severe in ((0.9, 2), (float("nan"), 2), (1.5, 1.4), (True, 2)):
            with self.subTest(stress=stress, severe=severe):
                with self.assertRaises(ValueError):
                    run_cost_stress_scenarios(
                        [{"t": 1}], base_fee_rate=0.001, base_slippage_rate=0.001,
                        stress_multiplier=stress, severe_stress_multiplier=severe,
                    )

    def test_rejects_costs_that_exceed_simulator_valid_range(self):
        with self.assertRaisesRegex(ValueError, "remain below 1"):
            run_cost_stress_scenarios(
                [{"t": 1}], base_fee_rate=0.7, base_slippage_rate=0.01,
                stress_multiplier=1.5, severe_stress_multiplier=2,
            )

    def test_rejects_duplicate_cost_arguments(self):
        with self.assertRaisesRegex(ValueError, "only through base rates"):
            run_cost_stress_scenarios(
                [{"t": 1}], base_fee_rate=0.001, base_slippage_rate=0.001,
                backtest_kwargs={"fee_rate": 0.002},
            )

    def test_real_simulator_metrics_match_independent_base_rerun(self):
        # Deterministic oscillating OHLC series gives the real simulator valid,
        # reproducible inputs without network or exchange-data dependencies.
        candles = []
        for i in range(180):
            close = 100 + (i % 24) * 0.8 + ((i // 24) % 2) * 3
            open_price = close - (0.25 if i % 2 else -0.25)
            candles.append({
                "t": i * 3_600_000,
                "o": open_price,
                "h": max(open_price, close) + 1.2,
                "l": min(open_price, close) - 1.2,
                "c": close,
                "v": 100,
                "q": 100 * close,
            })
        # Disable the mock for this integration-level regression.
        self.patch.stop()
        self.addCleanup(self.patch.start)
        common = {"fast": 3, "slow": 8, "atr_period": 3,
                  "fee_rate": 0.0005, "slippage_rate": 0.0002}
        expected = run_ema_cross_backtest(candles, **common)
        report = run_cost_stress_scenarios(
            candles, base_fee_rate=common["fee_rate"],
            base_slippage_rate=common["slippage_rate"],
            backtest_kwargs={"fast": 3, "slow": 8, "atr_period": 3},
        )
        base = report["scenarios"]["base"]
        self.assertEqual(base["status"], expected["status"])
        self.assertEqual(base["net_return_pct"], expected["net_return_pct"])
        self.assertEqual(base["max_drawdown_pct"], expected["max_drawdown_pct"])
        self.assertEqual(base["profit_factor"], expected["profit_factor"])
        self.assertEqual(base["equity_final"], expected["equity_final"])
        self.assertEqual(base["trades"], expected["trades"])
        self.assertEqual(report["scenarios"]["stress"]["fee_rate"], 0.00075)
        self.assertEqual(report["scenarios"]["severe_stress"]["slippage_rate"], 0.0004)


if __name__ == "__main__":
    unittest.main()
