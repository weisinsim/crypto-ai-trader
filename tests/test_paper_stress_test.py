import unittest

from research.paper_stress_test import run_paper_stress_scenarios


def candidate(trade_id="t1"):
    return {
        "trade_id": trade_id,
        "score": 78,
        "observation": {
            "symbol": "BTCUSDT", "signal": "LONG",
            "signal_candle_close_ms": 1000, "entry": 100,
            "sl": 95, "tp1": 105, "tp2": 110,
        },
        "future_candles": [
            {"t": 2000, "o": 100, "h": 103, "l": 99, "c": 102},
            {"t": 3000, "o": 102, "h": 106, "l": 101, "c": 105},
            {"t": 4000, "o": 105, "h": 111, "l": 104, "c": 110},
        ],
    }


class PaperStressTestTests(unittest.TestCase):
    def test_cost_scenarios_replay_same_candidate_separately(self):
        result = run_paper_stress_scenarios([candidate()])
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["scenario_count"], 3)
        reports = {item["scenario_id"]: item["report"] for item in result["scenarios"]}
        base = reports["base"]["completed_simulations"]["mean_net_return"]
        high = reports["high_cost"]["completed_simulations"]["mean_net_return"]
        self.assertGreater(base, high)
        self.assertFalse(result["historical_funding_verified"])
        self.assertIsNone(result["verified_score_calibration"])
        for scenario in result["scenarios"]:
            self.assertEqual(scenario["coverage"]["input_candidate_count"], 1)
            self.assertEqual(scenario["coverage"]["classified_count"], 1)
            self.assertTrue(scenario["coverage"]["coverage_complete"])

    def test_entry_gap_filtered_candidates_remain_visible_in_coverage(self):
        row = candidate("gap")
        row["future_candles"][0]["o"] = 106
        row["future_candles"][0]["h"] = 108
        row["future_candles"][0]["l"] = 105
        row["future_candles"][0]["c"] = 107
        result = run_paper_stress_scenarios([row])
        for scenario in result["scenarios"]:
            self.assertEqual(scenario["filtered_candidate_count"], 1)
            self.assertEqual(scenario["simulated_candidate_count"], 0)
            self.assertEqual(scenario["coverage"]["filtered_count"], 1)
            self.assertEqual(scenario["coverage"]["classified_count"], 1)
            self.assertTrue(scenario["coverage"]["coverage_complete"])
            self.assertEqual(scenario["filtered_candidates"][0]["reason"], "ENTRY_GAP_INVALIDATED")

    def test_duplicate_candidate_ids_after_whitespace_normalization_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate candidate"):
            run_paper_stress_scenarios([candidate("dup"), candidate(" dup ")])

    def test_duplicate_candidate_ids_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate candidate"):
            run_paper_stress_scenarios([candidate(), candidate()])

    def test_duplicate_scenario_ids_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate scenario_id"):
            run_paper_stress_scenarios(
                [candidate()],
                scenarios=[
                    {"scenario_id": "same"},
                    {"scenario_id": "same"},
                ],
            )

    def test_negative_cost_assumption_rejected(self):
        with self.assertRaisesRegex(ValueError, "nonnegative"):
            run_paper_stress_scenarios(
                [candidate()],
                scenarios=[{"scenario_id": "bad", "fee_bps_per_side": -1}],
            )

    def test_empty_candidates_are_safe(self):
        result = run_paper_stress_scenarios([])
        self.assertEqual(result["candidate_count"], 0)
        self.assertEqual(result["scenario_count"], 3)


if __name__ == "__main__":
    unittest.main()
