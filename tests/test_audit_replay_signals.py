import csv
import tempfile
import unittest
from pathlib import Path

from validation.audit_replay_signals import audit


class ReplaySignalAuditTests(unittest.TestCase):
    def write_csv(self, path, fields, rows):
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def test_accepts_signal_entry_at_source_close_and_correct_levels(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candles = root / "candles.csv"
            signals = root / "signals.csv"
            self.write_csv(candles, ["timestamp", "close"], [
                {"timestamp": "2025-01-01T00:59:59.999Z", "close": "100"}
            ])
            self.write_csv(signals, ["timestamp", "side", "entry", "atr", "stop", "target"], [
                {"timestamp": "2025-01-01T00:59:59.999Z", "side": "LONG",
                 "entry": "100", "atr": "2", "stop": "98", "target": "105"}
            ])
            result = audit("BTCUSDT", candles, signals)
            self.assertTrue(result["pass"])
            self.assertEqual(result["matched_signal_count"], 1)

    def test_rejects_entry_that_does_not_match_signal_candle_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candles = root / "candles.csv"
            signals = root / "signals.csv"
            self.write_csv(candles, ["timestamp", "close"], [
                {"timestamp": "2025-01-01T00:59:59.999Z", "close": "100"}
            ])
            self.write_csv(signals, ["timestamp", "side", "entry", "atr", "stop", "target"], [
                {"timestamp": "2025-01-01T00:59:59.999Z", "side": "LONG",
                 "entry": "101", "atr": "2", "stop": "99", "target": "106"}
            ])
            result = audit("BTCUSDT", candles, signals)
            self.assertFalse(result["pass"])
            self.assertEqual(result["errors_sample"][0]["error"], "ENTRY_NOT_MATCHED_TO_SIGNAL_CANDLE_CLOSE")

    def test_rejects_signal_timestamp_missing_from_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candles = root / "candles.csv"
            signals = root / "signals.csv"
            self.write_csv(candles, ["timestamp", "close"], [
                {"timestamp": "2025-01-01T00:59:59.999Z", "close": "100"}
            ])
            self.write_csv(signals, ["timestamp", "side", "entry", "atr", "stop", "target"], [
                {"timestamp": "2025-01-01T01:59:59.999Z", "side": "SHORT",
                 "entry": "100", "atr": "2", "stop": "102", "target": "95"}
            ])
            result = audit("BTCUSDT", candles, signals)
            self.assertFalse(result["pass"])
            self.assertEqual(result["errors_sample"][0]["error"], "SIGNAL_TIMESTAMP_NOT_IN_SOURCE_CANDLES")

    def test_rejects_wrong_side_stop_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candles = root / "candles.csv"
            signals = root / "signals.csv"
            self.write_csv(candles, ["timestamp", "close"], [
                {"timestamp": "2025-01-01T00:59:59.999Z", "close": "100"}
            ])
            self.write_csv(signals, ["timestamp", "side", "entry", "atr", "stop", "target"], [
                {"timestamp": "2025-01-01T00:59:59.999Z", "side": "LONG",
                 "entry": "100", "atr": "2", "stop": "101", "target": "105"}
            ])
            result = audit("BTCUSDT", candles, signals)
            self.assertFalse(result["pass"])
            self.assertEqual(result["errors_sample"][0]["error"], "STOP_TARGET_DIRECTION_INVALID")


if __name__ == "__main__":
    unittest.main()
