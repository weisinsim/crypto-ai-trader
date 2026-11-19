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


    async def test_overlapping_fills_are_deduplicated(self):
        async def source(**kwargs):
            fills = ([{"fill_id": "shared", "symbol": "BTC_USDT_PERP",
                       "timestamp_ms": 1000000}]
                     if kwargs["start_ms"] <= 1000000 <= kwargs["end_ms"]
                     else [])
            return {"pages": [{"request_cursor": None, "next_cursor": None,
                               "fills": fills}],
                    "terminal_page_verified": True,
                    "interval_coverage_verified": True}

        result = await inspect_recovery_sequence(
            1000000, 1800000, expected_symbol="BTC_USDT_PERP",
            fetch_verified_pages=source, max_span_ms=600000,
            overlap_ms=300000, max_windows=4)
        self.assertEqual(len(result["batches"]), 3)
        self.assertEqual(
            sum(len(batch["fills"]) for batch in result["batches"]), 1)
        self.assertFalse(result["notification_ready"])


    async def test_conflicting_duplicate_fill_fails_closed(self):
        calls = 0

        async def source(**kwargs):
            nonlocal calls
            calls += 1
            fill = {"fill_id": "shared", "symbol": "BTC_USDT_PERP",
                    "timestamp_ms": 1000000, "quantity": calls}
            return {"pages": [{"request_cursor": None, "next_cursor": None,
                               "fills": [fill] if kwargs["start_ms"] <= 1000000 <= kwargs["end_ms"] else []}],
                    "terminal_page_verified": True,
                    "interval_coverage_verified": True}

        with self.assertRaisesRegex(RecoverySequenceError, "Conflicting"):
            await inspect_recovery_sequence(
                1000000, 1800000, expected_symbol="BTC_USDT_PERP",
                fetch_verified_pages=source, max_span_ms=600000,
                overlap_ms=300000, max_windows=4)


    async def test_total_unique_fills_cap_across_windows(self):
        async def source(**kwargs):
            fill_id = str(kwargs["end_ms"])
            return {"pages": [{"request_cursor": None, "next_cursor": None,
                               "fills": [{"fill_id": fill_id,
                                          "symbol": "BTC_USDT_PERP",
                                          "timestamp_ms": kwargs["end_ms"]}]}],
                    "terminal_page_verified": True,
                    "interval_coverage_verified": True}
        with self.assertRaisesRegex(RecoverySequenceError, "Total unique"):
            await inspect_recovery_sequence(
                1000000, 2500000, expected_symbol="BTC_USDT_PERP",
                fetch_verified_pages=source, max_span_ms=600000,
                overlap_ms=100000, max_windows=4, max_fills=2)

    async def test_rejects_invalid_total_fill_cap(self):
        with self.assertRaises(RecoverySequenceError):
            await inspect_recovery_sequence(
                1000000, 2500000, expected_symbol="BTC_USDT_PERP",
                fetch_verified_pages=None, max_fills=0)


if __name__ == "__main__":
    unittest.main()
