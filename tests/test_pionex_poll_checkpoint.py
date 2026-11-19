import tempfile
import unittest
from pathlib import Path

from notifications.pionex_poll_checkpoint import CheckpointError, PionexPollCheckpoint


class PollCheckpointTests(unittest.TestCase):
    def test_restart_and_monotonic_verified_boundary(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "checkpoint.sqlite3"
            store = PionexPollCheckpoint(path)
            self.assertIsNone(store.last_verified("acct1", "BTC_USDT_PERP"))
            self.assertTrue(store.advance("acct1", "BTC_USDT_PERP", 100,
                pages_complete=True, positions_verified=True, fills_reconciled=True))
            self.assertFalse(store.advance("acct1", "BTC_USDT_PERP", 100,
                pages_complete=True, positions_verified=True, fills_reconciled=True))
            store.close()
            store = PionexPollCheckpoint(path)
            self.assertEqual(store.last_verified("acct1", "BTC_USDT_PERP"), 100)
            self.assertIsNone(store.last_verified("acct2", "BTC_USDT_PERP"))
            self.assertIsNone(store.last_verified("acct1", "ETH_USDT_PERP"))
            with self.assertRaises(CheckpointError):
                store.advance("acct1", "BTC_USDT_PERP", 99,
                    pages_complete=True, positions_verified=True, fills_reconciled=True)
            self.assertEqual(store.last_verified("acct1", "BTC_USDT_PERP"), 100)
            store.close()

    def test_partial_evidence_never_advances(self):
        with tempfile.TemporaryDirectory() as folder:
            store = PionexPollCheckpoint(Path(folder) / "checkpoint.sqlite3")
            for flags in (
                {"pages_complete": False, "positions_verified": True, "fills_reconciled": True},
                {"pages_complete": True, "positions_verified": False, "fills_reconciled": True},
                {"pages_complete": True, "positions_verified": True, "fills_reconciled": False},
            ):
                with self.assertRaises(CheckpointError):
                    store.advance("acct", "BTC_USDT_PERP", 100, **flags)
            self.assertIsNone(store.last_verified("acct", "BTC_USDT_PERP"))
            store.close()

    def test_invalid_timestamp_and_scope(self):
        with tempfile.TemporaryDirectory() as folder:
            store = PionexPollCheckpoint(Path(folder) / "checkpoint.sqlite3")
            for timestamp in (None, -1, 0, True, 1.5):
                with self.assertRaises(CheckpointError):
                    store.advance("acct", "BTC_USDT_PERP", timestamp,
                        pages_complete=True, positions_verified=True, fills_reconciled=True)
            with self.assertRaises(CheckpointError):
                store.last_verified("", "BTC_USDT_PERP")
            with self.assertRaises(CheckpointError):
                store.last_verified("acct", "BTC_USDT")
            store.close()


if __name__ == "__main__":
    unittest.main()
