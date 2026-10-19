"""Tests for the read-only SQLite outbox alert feed."""
import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import app as dashboard
from execution.alert_store import EntryAlertStore


class ChatGPTOutboxFeedTests(unittest.TestCase):
    def setUp(self):
        self.original_status = dict(dashboard.market_status)
        dashboard.market_status.update(status="LIVE", updated=time.time(), error=None)
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "alerts.sqlite3")
        now = time.time()
        self.event = {
            "event_id": "test-event-001", "eligible": True, "symbol": "BTCUSDT",
            "side": "LONG", "signal_candle_ms": int(now * 1000) - 60_000,
            "analysis_ts": now, "entry": 100.0, "stop_loss": 95.0,
            "take_profit_1": 110.0, "take_profit_2": 115.0, "score": 80,
            "model_status": "RESEARCH", "research_only": True,
            "notification_only": True, "auto_trade_authorized": False,
        }

    def tearDown(self):
        dashboard.market_status.clear()
        dashboard.market_status.update(self.original_status)
        self.tmp.cleanup()

    def test_reads_valid_pending_event_without_marking_delivered(self):
        store = EntryAlertStore(self.db)
        self.assertTrue(store.enqueue(self.event, now=time.time()))
        with patch.dict(os.environ, {"ALERT_OUTBOX_DB": self.db}):
            payload = asyncio.run(dashboard.chatgpt_outbox_alert_feed())
        self.assertEqual(payload["status"], "LIVE")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["alerts"][0]["symbol"], "BTCUSDT")
        self.assertTrue(payload["alerts"][0]["notification_only"])
        self.assertFalse(payload["alerts"][0]["auto_trade_authorized"])
        self.assertEqual(len(EntryAlertStore(self.db).pending()), 1)

    def test_fails_closed_on_non_live_or_stale_heartbeat(self):
        EntryAlertStore(self.db).enqueue(self.event, now=time.time())
        with patch.dict(os.environ, {"ALERT_OUTBOX_DB": self.db}):
            dashboard.market_status["status"] = "DELAYED"
            payload = asyncio.run(dashboard.chatgpt_outbox_alert_feed())
            self.assertEqual(payload["alerts"], [])
            dashboard.market_status.update(status="LIVE", updated=time.time() - 121)
            payload = asyncio.run(dashboard.chatgpt_outbox_alert_feed())
        self.assertEqual(payload["alerts"], [])
        self.assertIn("suppressed", payload["note"])

    def test_invalid_geometry_is_suppressed(self):
        self.event["stop_loss"] = 105.0
        EntryAlertStore(self.db).enqueue(self.event, now=time.time())
        with patch.dict(os.environ, {"ALERT_OUTBOX_DB": self.db}):
            payload = asyncio.run(dashboard.chatgpt_outbox_alert_feed())
        self.assertEqual(payload["alerts"], [])
        self.assertEqual(payload["count"], 0)


if __name__ == "__main__":
    unittest.main()
