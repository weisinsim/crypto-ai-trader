import unittest

from research.paper_trade_simulator import simulate_tactical_candidate


class PaperTradeSimulatorTests(unittest.TestCase):
    def candidate(self, **overrides):
        row = {
            "symbol": "BTCUSDT", "signal": "LONG",
            "signal_candle_close_ms": 3_600_000,
            "entry": 100.0, "sl": 95.0, "tp1": 105.0, "tp2": 110.0,
        }
        row.update(overrides)
        return row

    def candle(self, t, o=100, h=102, l=98, c=101):
        return {"t": t, "o": o, "h": h, "l": l, "c": c}

    def test_no_trade_for_watch_signal(self):
        result = simulate_tactical_candidate(
            observation=self.candidate(signal="WATCH-LONG"),
            future_candles=[self.candle(3_600_000)],
        )
        self.assertEqual(result["status"], "NO_TRADE")
        self.assertFalse(result["auto_trade_authorized"])

    def test_stop_wins_when_stop_and_target_touched_same_bar(self):
        result = simulate_tactical_candidate(
            observation=self.candidate(),
            future_candles=[self.candle(3_600_000, o=100, h=106, l=94, c=101)],
            fee_bps_per_side=0, slippage_bps_per_side=0, funding_bps_per_8h=0,
        )
        self.assertEqual(result["exit_reason"], "STOP")
        self.assertAlmostEqual(result["gross_pnl_per_unit"], -5.0)

    def test_partial_tp1_then_tp2_and_costs_are_explicit(self):
        result = simulate_tactical_candidate(
            observation=self.candidate(),
            future_candles=[
                self.candle(3_600_000, o=100, h=106, l=99, c=105),
                self.candle(7_200_000, o=105, h=111, l=104, c=110),
            ],
            fee_bps_per_side=0, slippage_bps_per_side=0, funding_bps_per_8h=0,
        )
        self.assertEqual([item["reason"] for item in result["exits"]], ["TP1", "TP2"])
        self.assertAlmostEqual(result["gross_pnl_per_unit"], 7.5)
        self.assertEqual(result["exit_reason"], "TP2")
        self.assertFalse(result["costs_verified"])
        self.assertFalse(result["fill_prices_verified"])

    def test_end_of_available_data_is_censored_not_a_completed_max_hold(self):
        result = simulate_tactical_candidate(
            observation=self.candidate(),
            future_candles=[self.candle(3_600_000, o=100, h=102, l=99, c=101)],
            fee_bps_per_side=0, slippage_bps_per_side=0, funding_bps_per_8h=0,
            max_holding_candles=12,
        )
        self.assertEqual(result["status"], "CENSORED")
        self.assertEqual(result["exit_reason"], "DATA_END")
        self.assertFalse(result["outcome_complete"])

    def test_rejects_invalid_levels_and_future_timestamps(self):
        with self.assertRaisesRegex(ValueError, "risk levels"):
            simulate_tactical_candidate(
                observation=self.candidate(sl=101),
                future_candles=[self.candle(3_600_000)],
            )
        with self.assertRaisesRegex(ValueError, "precedes signal close"):
            simulate_tactical_candidate(
                observation=self.candidate(),
                future_candles=[self.candle(0)],
            )



    def test_exit_slippage_reduces_long_and_short_returns(self):
        long_no_slip = simulate_tactical_candidate(
            observation=self.candidate(),
            future_candles=[self.candle(3_600_001, o=100, h=106, l=99, c=105)],
            fee_bps_per_side=0, slippage_bps_per_side=0, funding_bps_per_8h=0,
        )
        long_slip = simulate_tactical_candidate(
            observation=self.candidate(),
            future_candles=[self.candle(3_600_001, o=100, h=106, l=99, c=105)],
            fee_bps_per_side=0, slippage_bps_per_side=10, funding_bps_per_8h=0,
        )
        short_observation = self.candidate(
            signal="SHORT", entry=100.0, sl=105.0, tp1=95.0, tp2=90.0,
        )
        short_candles = [self.candle(3_600_001, o=100, h=101, l=94, c=95)]
        short_no_slip = simulate_tactical_candidate(
            observation=short_observation, future_candles=short_candles,
            fee_bps_per_side=0, slippage_bps_per_side=0, funding_bps_per_8h=0,
        )
        short_slip = simulate_tactical_candidate(
            observation=short_observation, future_candles=short_candles,
            fee_bps_per_side=0, slippage_bps_per_side=10, funding_bps_per_8h=0,
        )
        self.assertLess(long_slip["net_return_on_entry_notional"], long_no_slip["net_return_on_entry_notional"])
        self.assertLess(short_slip["net_return_on_entry_notional"], short_no_slip["net_return_on_entry_notional"])
        self.assertNotEqual(long_slip["exits"][-1]["price"], long_slip["exits"][-1]["reference_price"])


if __name__ == "__main__":
    unittest.main()
