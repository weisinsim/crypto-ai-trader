import json
import unittest
from pathlib import Path

from research.frozen_confirmation import FROZEN_PATH, SOURCE_DATA_END, sha256, utc_ms


class FrozenConfirmationTests(unittest.TestCase):
    def test_frozen_config_has_only_the_two_declared_candidates(self):
        config = json.loads(FROZEN_PATH.read_text(encoding="utf-8"))
        self.assertEqual(set(config["candidates"]), {"BTCUSDT", "AVAXUSDT"})
        self.assertFalse(config["confirmation_policy"]["parameter_search_allowed"])
        self.assertFalse(config["confirmation_policy"]["live_trading_allowed"])

    def test_frozen_config_hash_is_stable_for_current_file(self):
        digest = sha256(FROZEN_PATH)
        self.assertEqual(len(digest), 64)
        self.assertTrue(all(char in "0123456789abcdef" for char in digest))

    def test_confirmation_window_boundary_is_explicit_utc(self):
        self.assertEqual(utc_ms("2026-10-01"), int(SOURCE_DATA_END.timestamp() * 1000))
        self.assertLess(utc_ms("2026-10-01"), utc_ms("2026-10-02"))


if __name__ == "__main__":
    unittest.main()
