"""Offline tests: never connect to an exchange."""
import asyncio
import unittest

from notifications.pionex_bounded_backfill import fetch_bounded_fills, FillBackfillError


def fill(fid, ts):
    return {"id": fid, "orderId": "order-1", "symbol": "BTC_USDT_PERP",
            "side": "SELL", "size": "0.1", "timestamp": ts}


class FakeReader:
    def __init__(self, fills):
        self.rows = fills
        self.calls = []

    async def fills(self, symbol, **filters):
        self.calls.append(filters)
        return {"fills": [f for f in self.rows if
                filters["startTime"] <= f["timestamp"] <= filters["endTime"]][:filters["limit"]]}


class BoundedBackfillTests(unittest.TestCase):
    def run_fetch(self, reader, start=100, end=109, **kwargs):
        return asyncio.run(fetch_bounded_fills(reader, "BTC_USDT_PERP", start, end, **kwargs))

    def test_empty_window(self):
        reader = FakeReader([])
        self.assertEqual(self.run_fetch(reader), [])
        self.assertEqual(len(reader.calls), 1)

    def test_saturated_window_splits_and_recovers_all(self):
        reader = FakeReader([fill("a", 100), fill("b", 101), fill("c", 102)])
        result = self.run_fetch(reader, limit=2)
        self.assertEqual([x["fill_id"] for x in result], ["a", "b", "c"])
        self.assertGreater(len(reader.calls), 1)

    def test_dense_single_millisecond_fails_closed(self):
        reader = FakeReader([fill("a", 100), fill("b", 100)])
        with self.assertRaisesRegex(FillBackfillError, "Single millisecond"):
            self.run_fetch(reader, 100, 100, limit=2)

    def test_request_budget_fails_closed(self):
        reader = FakeReader([fill("a", 100), fill("b", 101)])
        with self.assertRaisesRegex(FillBackfillError, "budget"):
            self.run_fetch(reader, limit=1, max_requests=1)

    def test_duplicate_identity_across_disjoint_windows_fails_closed(self):
        class InconsistentReader:
            async def fills(self, symbol, **filters):
                lo, hi = filters["startTime"], filters["endTime"]
                if hi - lo > 1:
                    return {"fills": [fill("a", lo), fill("b", hi)]}
                return {"fills": [fill("same-id", lo)]}
        with self.assertRaisesRegex(FillBackfillError, "Repeated fill identity"):
            self.run_fetch(InconsistentReader(), 100, 103, limit=2)

    def test_boundary_timestamp_is_not_lost_when_split(self):
        reader = FakeReader([fill("a", 100), fill("b", 104),
                             fill("c", 105), fill("d", 109)])
        result = self.run_fetch(reader, limit=2)
        self.assertEqual([x["timestamp_ms"] for x in result], [100, 104, 105, 109])

    def test_invalid_response_fails_closed(self):
        class BadReader:
            async def fills(self, *args, **kwargs):
                return {"unexpected": []}
        with self.assertRaises(FillBackfillError):
            self.run_fetch(BadReader())

    def test_bad_fill_in_one_split_window_rejects_whole_backfill(self):
        class CorruptReader:
            async def fills(self, symbol, **filters):
                lo, hi = filters["startTime"], filters["endTime"]
                if hi - lo > 1:
                    return {"fills": [fill("a", lo), fill("b", hi)]}
                if lo >= 102:
                    return {"fills": [fill("bad", lo - 1)]}
                return {"fills": [fill("good", lo)]}
        with self.assertRaisesRegex(FillBackfillError, "Unverified fill window"):
            self.run_fetch(CorruptReader(), 100, 103, limit=2)

    def test_rejects_malformed_fill_without_partial_results(self):
        class CorruptReader:
            async def fills(self, symbol, **filters):
                return {"fills": [{"id": "x", "symbol": symbol}]}
        with self.assertRaisesRegex(FillBackfillError, "Unverified fill window"):
            self.run_fetch(CorruptReader())

    def test_invalid_input_rejected_before_request(self):
        reader = FakeReader([])
        with self.assertRaises(FillBackfillError):
            self.run_fetch(reader, max_requests=True)
        self.assertEqual(reader.calls, [])


if __name__ == "__main__":
    unittest.main()
