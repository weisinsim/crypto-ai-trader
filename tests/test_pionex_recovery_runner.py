import unittest

from notifications.pionex_recovery_runner import (
    RecoveryRunnerError, run_recovery_once,
)


class RecoveryRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_valid_normalized_source(self):
        seen = []

        async def source(**kwargs):
            seen.append(kwargs)
            return {
                "pages": [{"request_cursor": None, "next_cursor": None,
                           "fills": [{"fill_id": "f1", "symbol": "BTC_USDT_PERP",
                                      "timestamp_ms": 900000}]}],
                "terminal_page_verified": True,
                "interval_coverage_verified": True,
            }

        result = await run_recovery_once(
            1000000, 1100000, expected_symbol="BTC_USDT_PERP",
            fetch_verified_pages=source)
        self.assertEqual(seen[0]["start_ms"], 700000)
        self.assertEqual(seen[0]["end_ms"], 1100000)
        self.assertFalse(result["checkpoint_advanced"])
        self.assertFalse(result["notification_ready"])

    async def test_rejects_unverified_interval(self):
        async def source(**kwargs):
            return {"pages": [{"request_cursor": None, "next_cursor": None,
                               "fills": []}],
                    "terminal_page_verified": True,
                    "interval_coverage_verified": False}
        with self.assertRaises(ValueError):
            await run_recovery_once(1000000, 1100000,
                                    expected_symbol="BTC_USDT_PERP",
                                    fetch_verified_pages=source)

    async def test_rejects_unexpected_evidence(self):
        async def source(**kwargs):
            return {"pages": [], "terminal_page_verified": True,
                    "interval_coverage_verified": True, "extra": True}
        with self.assertRaises(RecoveryRunnerError):
            await run_recovery_once(1000000, 1100000,
                                    expected_symbol="BTC_USDT_PERP",
                                    fetch_verified_pages=source)

    async def test_rejects_sync_source(self):
        with self.assertRaises(RecoveryRunnerError):
            await run_recovery_once(1000000, 1100000,
                                    expected_symbol="BTC_USDT_PERP",
                                    fetch_verified_pages=lambda **kw: {})

    async def test_rejects_missing_source(self):
        with self.assertRaises(RecoveryRunnerError):
            await run_recovery_once(1000000, 1100000,
                                    expected_symbol="BTC_USDT_PERP",
                                    fetch_verified_pages=None)


if __name__ == "__main__":
    unittest.main()
