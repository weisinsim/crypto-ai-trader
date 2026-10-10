import unittest
from validation.promotion_gate import classify_candidate

class PromotionGateTests(unittest.TestCase):
    def test_insufficient_holdout_trades_fails(self):
        self.assertEqual(classify_candidate(5, 6, 29, 1.5), 'FAIL_INSUFFICIENT_HOLDOUT_TRADES')

    def test_development_loss_fails_even_if_holdout_wins(self):
        self.assertEqual(classify_candidate(-1, 6, 50, 1.5), 'FAIL_NONPOSITIVE_DEVELOPMENT_OR_HOLDOUT')

    def test_holdout_loss_fails(self):
        self.assertEqual(classify_candidate(5, -1, 50, 1.5), 'FAIL_NONPOSITIVE_DEVELOPMENT_OR_HOLDOUT')

    def test_profit_factor_not_above_one_fails(self):
        self.assertEqual(classify_candidate(5, 6, 50, 1.0), 'FAIL_HOLDOUT_PROFIT_FACTOR')

    def test_missing_metrics_fail(self):
        self.assertEqual(classify_candidate(float('nan'), 6, 50, 1.5), 'FAIL_MISSING_OR_INVALID_NET_R')

    def test_positive_values_are_only_preliminary_pass(self):
        self.assertEqual(classify_candidate(5, 6, 50, 1.5), 'PASS_PRELIMINARY_SCREEN_ONLY')

if __name__ == '__main__':
    unittest.main()
