import unittest

from notifications.pionex_recovery_sequence import (
    RecoverySequenceError, inspect_recovery_sequence,
)


class RecoverySequenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_multi_window_never_advances_checkpoint(self):
        calls = []

        async def source(**kwargs):
            calls.append(kwargs)
            return {"pages": [{"request_cursor": None, "next_cursor": None,
                               "fills": []}],
                    "terminal_page_verified": True,
                    "interval_coverage_verified": True}

        result = await inspect_recovery_sequence(
            1000000, 2500000, expected_symbol="BTC_USDT_PERP",
            fetch_verified_pages=source, max_span_ms=600000,
            overlap_ms=100000, max_windows=4)
        self.assertEqual(len(calls), 3)
        self.assertEqual(result["candidate_end_ms"], 2500000)
        self.assertFalse(result["more_history_pending"])
        self.assertFalse(result["checkpoint_advanced"])
        self.assertFalse(result["notification_ready"])

    async def test_cap_stops_before_unverified_future(self):
        async def source(**kwargs):
            return {"pages": [{"request_cursor": None, "next_cursor": None,
                               "fills": []}],
                    "terminal_page_verified": True,
                    "interval_coverage_verified": True}

        result = await inspect_recovery_sequence(
            1000000, 2500000, expected_symbol="BTC_USDT_PERP",
            fetch_verified_pages=source, max_span_ms=600000,
            overlap_ms=100000, max_windows=1)
        self.assertTrue(result["more_history_pending"])
        self.assertEqual(len(result["batches"]), 1)

    async def test_invalid_cap(self):
        with self.assertRaises(RecoverySequenceError):
            await inspect_recovery_sequence(
                1000000, 2500000, expected_symbol="BTC_USDT_PERP",
                fetch_verified_pages=None, max_windows=0)

    async def test_unverified_window_aborts(self):
        async def source(**kwargs):
            return {"pages": [{"request_cursor": None, "next_cursor": None,
                               "fills": []}],
                    "terminal_page_verified": True,
                    "interval_coverage_verified": False}
        with self.assertRaises(ValueError):
            await inspect_recovery_sequence(
                1000000, 2500000, expected_symbol="BTC_USDT_PERP",
                fetch_verified_pages=source)


if __name__ == "__main__":
    unittest.main()
