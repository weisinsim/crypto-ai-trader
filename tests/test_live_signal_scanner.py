"""Tests for live closed-candle scan bridge."""
import json
import unittest

from execution.live_signal_scanner import BinanceUsdMMarketData, MarketDataError, scan_once


NOW = 1_800_000_100_000
CANDLE_CLOSE_BOUNDARY = 1_799_996_400_000


class Response:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.body).encode()


class MarketDataTests(unittest.TestCase):
    def test_returns_latest_closed_candle_boundary_not_open_candle(self):
        rows = [
            [CANDLE_CLOSE_BOUNDARY - 3_600_000, 0, 0, 0, 0, 0, CANDLE_CLOSE_BOUNDARY - 1],
            [CANDLE_CLOSE_BOUNDARY, 0, 0, 0, 0, 0, CANDLE_CLOSE_BOUNDARY + 3_600_000 - 1],
        ]
        client = BinanceUsdMMarketData(opener=lambda *a, **k: Response(rows))
        self.assertEqual(
            client.last_closed_candle_ms("BTCUSDT", "1h", now_ms=CANDLE_CLOSE_BOUNDARY + 1_000),
            CANDLE_CLOSE_BOUNDARY,
        )

    def test_rejects_empty_market_data(self):
        client = BinanceUsdMMarketData(opener=lambda *a, **k: Response([]))
        with self.assertRaises(MarketDataError):
            client.last_closed_candle_ms("BTCUSDT", "1h", now_ms=NOW)


class ScanOnceTests(unittest.TestCase):
    def setUp(self):
        self.market = type("Market", (), {
            "last_closed_candle_ms": lambda _self, symbol, interval, now_ms=None: CANDLE_CLOSE_BOUNDARY
        })()
        self.snapshot = {
            "symbol": "BTCUSDT", "signal": "LONG", "score": 82,
            "model_status": "RESEARCH", "analysis_ts": NOW / 1000,
            "confirmed_signal_time": CANDLE_CLOSE_BOUNDARY,
            "entry": 65000.0, "sl": 64500.0, "tp1": 66000.0, "tp2": 66500.0,
            "rr": 2.0, "model_name": "test", "model_version": "test-v1",
        }

    def test_queues_valid_candle_aligned_signal(self):
        class Store:
            def __init__(self):
                self.events = []
            def enqueue(self, event, now=None):
                self.events.append(event)
                return True
        store = Store()
        result = scan_once(
            symbols=["BTCUSDT"], interval="1h", market_data=self.market,
            snapshot_provider=lambda symbol: dict(self.snapshot), store=store, now_ms=NOW,
        )
        self.assertEqual(result["queued"], 1)
        self.assertEqual(len(store.events), 1)
        self.assertTrue(store.events[0]["research_only"])
        self.assertFalse(store.events[0]["auto_trade_authorized"])

    def test_rejects_signal_not_for_latest_closed_candle(self):
        self.snapshot["confirmed_signal_time"] -= 3_600_000
        class Store:
            def enqueue(self, event, now=None):
                raise AssertionError("stale signal must never be queued")
        result = scan_once(
            symbols=["BTCUSDT"], interval="1h", market_data=self.market,
            snapshot_provider=lambda symbol: dict(self.snapshot), store=Store(), now_ms=NOW,
        )
        self.assertEqual(result["rejected"], 1)
        self.assertEqual(result["queued"], 0)

    def test_isolates_symbol_errors(self):
        class BrokenMarket:
            def last_closed_candle_ms(self, *args, **kwargs):
                raise MarketDataError("do not expose this")
        result = scan_once(
            symbols=["BTCUSDT"], interval="1h", market_data=BrokenMarket(),
            snapshot_provider=lambda symbol: {}, store=object(), now_ms=NOW,
        )
        self.assertEqual(result["scanned"], 1)
        self.assertEqual(result["errors"][0]["reason"], "MarketDataError")
        self.assertNotIn("do not expose this", str(result))


if __name__ == "__main__":
    unittest.main()
