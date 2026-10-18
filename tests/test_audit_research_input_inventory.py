import hashlib
import tempfile
import unittest
from pathlib import Path

from validation.audit_research_input_inventory import build_inventory, inspect_csv


class ResearchInputInventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.candles = self.root / "candles"
        self.funding = self.root / "funding"
        self.candles.mkdir()
        self.funding.mkdir()

    def test_missing_file_fails_closed(self):
        result = inspect_csv(self.candles / "BTCUSDT_1h.csv", "candle",
                             1735689600000, 1735693200000, "1h")
        self.assertEqual(result["status"], "MISSING")
        self.assertFalse(result["pass"])

    def test_candle_window_checks_exact_close_timestamps_and_fingerprint(self):
        path = self.candles / "BTCUSDT_1h.csv"
        payload = (
            "timestamp,open,high,low,close\n"
            "2025-01-01T00:59:59.999Z,100,102,99,101\n"
        )
        path.write_text(payload, encoding="utf-8")
        result = inspect_csv(path, "candle", 1735689600000, 1735693200000, "1h")
        self.assertTrue(result["pass"])
        self.assertEqual(result["status"], "PASS_CANDLE_WINDOW")
        self.assertEqual(result["sha256"], hashlib.sha256(payload.encode()).hexdigest())
        self.assertEqual(result["size_bytes"], len(payload.encode()))

    def test_non_aligned_window_uses_epoch_anchored_candle_closes(self):
        path = self.candles / "BTCUSDT_1h.csv"
        path.write_text(
            "timestamp,open,high,low,close\\n"
            "2025-01-01T01:59:59.999Z,100,102,99,101\\n",
            encoding="utf-8")
        # Window begins at 00:30, so the 00:59 close is expected; this input
        # starts too late and must not be incorrectly accepted as complete.
        result = inspect_csv(path, "candle", 1735691400000, 1735698600000, "1h")
        self.assertFalse(result["pass"])
        self.assertEqual(result["expected_rows"], 2)
        self.assertEqual(result["missing_expected_rows"], 1)
        self.assertEqual(result["missing_examples_ms"], [1735693199999])

    def test_non_aligned_window_accepts_all_closes_inside_range(self):
        path = self.candles / "BTCUSDT_1h.csv"
        path.write_text(
            "timestamp,open,high,low,close\\n"
            "2025-01-01T00:59:59.999Z,100,102,99,101\\n"
            "2025-01-01T01:59:59.999Z,101,103,100,102\\n",
            encoding="utf-8")
        result = inspect_csv(path, "candle", 1735691400000, 1735698600000, "1h")
        self.assertTrue(result["pass"])
        self.assertEqual(result["expected_rows"], 2)

    def test_duplicate_and_out_of_order_timestamps_fail(self):
        path = self.candles / "BTCUSDT_1h.csv"
        path.write_text(
            "timestamp,open,high,low,close\n"
            "2025-01-01T00:59:59.999Z,100,102,99,101\n"
            "2025-01-01T00:59:59.999Z,100,102,99,101\n",
            encoding="utf-8")
        result = inspect_csv(path, "candle", 1735689600000, 1735696800000, "1h")
        self.assertFalse(result["pass"])
        self.assertEqual(result["status"], "TIMESTAMP_INTEGRITY_FAILED")

    def test_missing_candle_window_rows_fail(self):
        path = self.candles / "BTCUSDT_1h.csv"
        path.write_text(
            "timestamp,open,high,low,close\n"
            "2025-01-01T00:59:59.999Z,100,102,99,101\n",
            encoding="utf-8")
        result = inspect_csv(path, "candle", 1735689600000, 1735696800000, "1h")
        self.assertFalse(result["pass"])
        self.assertEqual(result["missing_expected_rows"], 1)

    def test_funding_inventory_does_not_claim_reconciliation(self):
        path = self.funding / "BTCUSDT_funding.csv"
        path.write_text(
            "timestamp,funding_rate\n"
            "2025-01-01T00:00:00Z,0.0001\n",
            encoding="utf-8")
        result = inspect_csv(path, "funding", 1735689600000, 1735776000000)
        self.assertTrue(result["pass"])
        self.assertEqual(result["status"], "FUNDING_INVENTORIED_NOT_RECONCILED")

    def test_batch_missing_any_required_input_is_not_passed(self):
        result = build_inventory(self.candles, self.funding,
                                 "2025-01-01T00:00:00Z", "2025-01-02T00:00:00Z",
                                 ["BTCUSDT"])
        self.assertFalse(result["all_inputs_inventory_pass"])
        self.assertFalse(result["profitability_claim_eligible"])
        self.assertFalse(result["strategy_approval"])
        self.assertFalse(result["live_trading_enabled"])

    def test_invalid_ohlc_relationship_fails(self):
        path = self.candles / "BTCUSDT_1h.csv"
        path.write_text(
            "timestamp,open,high,low,close\n"
            "2025-01-01T00:59:59.999Z,100,98,99,101\n",
            encoding="utf-8")
        result = inspect_csv(path, "candle", 1735689600000, 1735693200000, "1h")
        self.assertFalse(result["pass"])
        self.assertEqual(result["status"], "INVALID")


if __name__ == "__main__":
    unittest.main()
