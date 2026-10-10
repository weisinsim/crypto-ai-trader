import unittest

from validation.run_multi_asset_exit_audit import holdout_max_trade_count


class BatchAuditTests(unittest.TestCase):
    def test_ignores_metadata_fields(self):
        holdout = {
            "signal_rows": 12,
            "target_2.5R": {"trades": 8, "net_R": 2.1},
            "target_3.0R": {"trades": 9, "net_R": 2.8},
        }
        self.assertEqual(holdout_max_trade_count(holdout), 9)

    def test_empty_or_metadata_only_holdout_returns_zero(self):
        self.assertEqual(holdout_max_trade_count({}), 0)
        self.assertEqual(holdout_max_trade_count({"signal_rows": 4}), 0)


if __name__ == "__main__":
    unittest.main()
