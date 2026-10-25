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
            future_candles=[self.candle(3_600_001)],
        )
        self.assertEqual(result["status"], "NO_TRADE")
        self.assertFalse(result["auto_trade_authorized"])

    def test_stop_wins_when_stop_and_target_touched_same_bar(self):
        result = simulate_tactical_candidate(
            observation=self.candidate(),
            future_candles=[self.candle(3_600_001, o=100, h=106, l=94, c=101)],
            fee_bps_per_side=0, slippage_bps_per_side=0, funding_bps_per_8h=0,
        )
        self.assertEqual(result["exit_reason"], "STOP")
        self.assertAlmostEqual(result["gross_pnl_per_unit"], -5.0)

    def test_partial_tp1_then_tp2_and_costs_are_explicit(self):
        result = simulate_tactical_candidate(
            observation=self.candidate(),
            future_candles=[
                self.candle(3_600_001, o=100, h=106, l=99, c=105),
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
            future_candles=[self.candle(3_600_001, o=100, h=102, l=99, c=101)],
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
                future_candles=[self.candle(3_600_001)],
            )
        with self.assertRaisesRegex(ValueError, "must be after signal close"):
            simulate_tactical_candidate(
                observation=self.candidate(),
                future_candles=[self.candle(3_600_000)],
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



    def test_entry_gap_beyond_stop_or_first_target_is_filtered(self):
        cases = [
            (self.candidate(), self.candle(3_600_001, o=94, h=96, l=93, c=95)),
            (self.candidate(), self.candle(3_600_001, o=106, h=108, l=105, c=107)),
            (self.candidate(signal="SHORT", entry=100, sl=105, tp1=95, tp2=90),
             self.candle(3_600_001, o=106, h=108, l=105, c=107)),
            (self.candidate(signal="SHORT", entry=100, sl=105, tp1=95, tp2=90),
             self.candle(3_600_001, o=94, h=96, l=93, c=95)),
        ]
        for observation, first_candle in cases:
            with self.subTest(side=observation["signal"], open=first_candle["o"]):
                result = simulate_tactical_candidate(
                    observation=observation, future_candles=[first_candle],
                    fee_bps_per_side=0, slippage_bps_per_side=0, funding_bps_per_8h=0,
                )
                self.assertEqual(result["status"], "NO_TRADE")
                self.assertEqual(result["reason"], "ENTRY_GAP_INVALIDATED")
                self.assertFalse(result["outcome_complete"])
                self.assertFalse(result["auto_trade_authorized"])

    def test_entry_slippage_that_crosses_stop_or_target_filters_candidate(self):
        long_result = simulate_tactical_candidate(
            observation=self.candidate(),
            future_candles=[self.candle(3_600_001, o=100, h=101, l=99, c=100)],
            fee_bps_per_side=0, slippage_bps_per_side=600, funding_bps_per_8h=0,
        )
        self.assertEqual(long_result["status"], "NO_TRADE")
        self.assertEqual(long_result["reason"], "ENTRY_SLIPPAGE_INVALIDATED")
        self.assertFalse(long_result["outcome_complete"])

        short_result = simulate_tactical_candidate(
            observation=self.candidate(signal="SHORT", entry=100, sl=105, tp1=95, tp2=90),
            future_candles=[self.candle(3_600_001, o=100, h=101, l=99, c=100)],
            fee_bps_per_side=0, slippage_bps_per_side=600, funding_bps_per_8h=0,
        )
        self.assertEqual(short_result["status"], "NO_TRADE")
        self.assertEqual(short_result["reason"], "ENTRY_SLIPPAGE_INVALIDATED")
        self.assertFalse(short_result["outcome_complete"])

    def test_gap_through_stop_uses_worse_open_reference(self):
        long_result = simulate_tactical_candidate(
            observation=self.candidate(),
            future_candles=[self.candle(3_600_001), self.candle(7_200_000, o=90, h=92, l=89, c=91)],
            fee_bps_per_side=0, slippage_bps_per_side=0, funding_bps_per_8h=0,
        )
        self.assertEqual(long_result["exit_reason"], "STOP")
        self.assertEqual(long_result["exits"][0]["reference_price"], 90)
        self.assertTrue(long_result["exits"][0]["gap_through_stop"])
        self.assertAlmostEqual(long_result["gross_pnl_per_unit"], -10.0)

        short_result = simulate_tactical_candidate(
            observation=self.candidate(
                signal="SHORT", entry=100, sl=105, tp1=95, tp2=90,
            ),
            future_candles=[self.candle(3_600_001), self.candle(7_200_000, o=110, h=111, l=108, c=109)],
            fee_bps_per_side=0, slippage_bps_per_side=0, funding_bps_per_8h=0,
        )
        self.assertEqual(short_result["exits"][0]["reference_price"], 110)
        self.assertTrue(short_result["exits"][0]["gap_through_stop"])
        self.assertAlmostEqual(short_result["gross_pnl_per_unit"], -10.0)


if __name__ == "__main__":
    unittest.main()
