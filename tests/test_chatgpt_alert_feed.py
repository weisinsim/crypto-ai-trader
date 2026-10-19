"""Tests for the read-only ChatGPT alert feed endpoint."""
import asyncio
import time
import unittest

import app as dashboard


class ChatGPTAlertFeedTests(unittest.TestCase):
    def setUp(self):
        self.original_cache = {key: dict(value) for key, value in dashboard.cache.items()}
        now = time.time()
        for symbol, item in dashboard.cache.items():
            item.update({
                "confirmed_signal": "NO-TRADE",
                "confirmed_signal_time": None,
                "analysis_ts": 0,
                "confirmed_entry": None,
                "confirmed_sl": None,
                "confirmed_tp1": None,
                "confirmed_tp2": None,
                "confirmed_score": 0,
                "confirmed_score_label": "NO-TRADE",
                "model_name": "test-model",
                "model_status": "RESEARCH",
            })
        dashboard.market_status["status"] = "LIVE"
        dashboard.cache["BTCUSDT"].update({
            "confirmed_signal": "LONG",
            "confirmed_signal_time": int(now * 1000) - 60_000,
            "analysis_ts": now,
            "confirmed_entry": 100.0,
            "confirmed_sl": 95.0,
            "confirmed_tp1": 110.0,
            "confirmed_tp2": 115.0,
            "confirmed_score": 80,
            "confirmed_score_label": "A",
        })

    def tearDown(self):
        for key, value in self.original_cache.items():
            dashboard.cache[key].clear()
            dashboard.cache[key].update(value)

    def test_includes_fresh_confirmed_candidate_without_trade_authorization(self):
        payload = asyncio.run(dashboard.chatgpt_alert_feed())
        self.assertEqual(payload["count"], 1)
        alert = payload["alerts"][0]
        self.assertEqual(alert["symbol"], "BTCUSDT")
        self.assertEqual(alert["side"], "LONG")
        self.assertEqual(len(alert["event_id"]), 24)
        self.assertEqual(alert["event_id"], asyncio.run(dashboard.chatgpt_alert_feed())["alerts"][0]["event_id"])
        self.assertTrue(alert["notification_only"])
        self.assertFalse(alert["auto_trade_authorized"])
        self.assertTrue(alert["research_only"])

    def test_excludes_stale_and_invalid_stop_geometry(self):
        dashboard.cache["BTCUSDT"]["confirmed_signal_time"] = int(time.time() * 1000) - 2 * 60 * 60 * 1000
        payload = asyncio.run(dashboard.chatgpt_alert_feed())
        self.assertEqual(payload["count"], 0)
        dashboard.cache["BTCUSDT"].update({
            "confirmed_signal_time": int(time.time() * 1000) - 60_000,
            "confirmed_sl": 105.0,
        })
        payload = asyncio.run(dashboard.chatgpt_alert_feed())
        self.assertEqual(payload["count"], 0)

    def test_suppresses_cached_candidates_when_market_is_not_live(self):
        dashboard.market_status["status"] = "DELAYED"
        payload = asyncio.run(dashboard.chatgpt_alert_feed())
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["alerts"], [])
        self.assertIn("not LIVE", payload["note"])

    def test_excludes_non_directional_signals(self):
        dashboard.cache["BTCUSDT"]["confirmed_signal"] = "NO-TRADE"
        payload = asyncio.run(dashboard.chatgpt_alert_feed())
        self.assertEqual(payload["alerts"], [])


if __name__ == "__main__":
    unittest.main()
