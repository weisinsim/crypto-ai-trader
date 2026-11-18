import unittest

from notifications.pionex_recovery_batch import (
    RecoveryBatchError, prepare_recovery_batch,
)


class RecoveryBatchTests(unittest.TestCase):
    def setUp(self):
        self.symbol = "BTC_USDT_PERP"
        self.pages = [{
            "request_cursor": None, "next_cursor": None,
            "fills": [{"fill_id": "abc", "symbol": self.symbol,
                       "timestamp_ms": 900000}],
        }]

    def batch(self, **kwargs):
        params = dict(expected_symbol=self.symbol,
                      terminal_page_verified=True,
                      interval_coverage_verified=True)
        params.update(kwargs)
        return prepare_recovery_batch(1000000, 1100000, self.pages, **params)

    def test_valid_batch_never_commits_or_notifies(self):
        batch = self.batch()
        self.assertFalse(batch["checkpoint_advanced"])
        self.assertFalse(batch["notification_ready"])
        self.assertEqual(len(batch["fills"]), 1)

    def test_requires_terminal_evidence(self):
        with self.assertRaises(RecoveryBatchError):
            self.batch(terminal_page_verified=False)

    def test_requires_interval_coverage(self):
        with self.assertRaises(RecoveryBatchError):
            self.batch(interval_coverage_verified=False)

    def test_rejects_outside_window(self):
        self.pages[0]["fills"][0]["timestamp_ms"] = 699999
        with self.assertRaises(RecoveryBatchError):
            self.batch()

    def test_rejects_missing_timestamp(self):
        del self.pages[0]["fills"][0]["timestamp_ms"]
        with self.assertRaises(RecoveryBatchError):
            self.batch()

    def test_rejects_unfetched_page(self):
        self.pages[0]["next_cursor"] = "next"
        with self.assertRaises(ValueError):
            self.batch()


    def test_rejects_duplicate_exchange_fill_id(self):
        self.pages[0]["fills"].append(dict(self.pages[0]["fills"][0]))
        with self.assertRaises(ValueError):
            self.batch()

    def test_rejects_wrong_contract(self):
        self.pages[0]["fills"][0]["symbol"] = "ETH_USDT_PERP"
        with self.assertRaises(ValueError):
            self.batch()

    def test_accepts_empty_terminal_page_with_verified_coverage(self):
        self.pages[0]["fills"] = []
        batch = self.batch()
        self.assertEqual(batch["fills"], [])
        self.assertFalse(batch["checkpoint_advanced"])

    def test_rejects_boolean_timestamp(self):
        self.pages[0]["fills"][0]["timestamp_ms"] = True
        with self.assertRaises(RecoveryBatchError):
            self.batch()

    def test_rejects_future_fill(self):
        self.pages[0]["fills"][0]["timestamp_ms"] = 1100001
        with self.assertRaises(RecoveryBatchError):
            self.batch()


if __name__ == "__main__":
    unittest.main()
