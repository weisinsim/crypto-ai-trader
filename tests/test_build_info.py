"""Deployment metadata endpoint must report unknown revisions honestly and keep trading disabled."""
import asyncio
import os
import unittest
from unittest.mock import patch

import app as dashboard


class BuildInfoTests(unittest.TestCase):
    def test_unknown_revision_is_not_guessed_and_trading_stays_disabled(self):
        with patch.dict(os.environ, {}, clear=True):
            payload = asyncio.run(dashboard.build_info())
        self.assertIsNone(payload["revision"])
        self.assertIsNone(payload["revision_source"])
        self.assertFalse(payload["revision_known"])
        self.assertFalse(payload["auto_trade_enabled"])
        self.assertEqual(payload["mode"], "research-gated")

    def test_render_revision_is_reported_exactly(self):
        with patch.dict(os.environ, {"RENDER_GIT_COMMIT": "abcdef1234567890"}, clear=True):
            payload = asyncio.run(dashboard.build_info())
        self.assertEqual(payload["revision"], "abcdef1234567890")
        self.assertEqual(payload["revision_source"], "RENDER_GIT_COMMIT")
        self.assertTrue(payload["revision_known"])
        self.assertFalse(payload["auto_trade_enabled"])

    def test_generic_revision_fallback_is_supported(self):
        with patch.dict(os.environ, {"GIT_COMMIT": "1234567890"}, clear=True):
            payload = asyncio.run(dashboard.build_info())
        self.assertEqual(payload["revision"], "1234567890")
        self.assertEqual(payload["revision_source"], "GIT_COMMIT")


if __name__ == "__main__":
    unittest.main()
