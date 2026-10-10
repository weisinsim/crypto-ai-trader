import csv
import tempfile
import unittest
from pathlib import Path

from validation.run_multi_asset_exit_audit import check_candle_integrity


class BatchAuditTests(unittest.TestCase):
    def test_ignores_metadata_fields(self):
        from validation.run_multi_asset_exit_audit import holdout_max_trade_count
        holdout = {
            "signal_rows": 12,
            "target_2.5R": {"trades": 8, "net_R": 2.1},
            "target_3.0R": {"trades": 9, "net_R": 2.8},
        }
        self.assertEqual(holdout_max_trade_count(holdout), 9)

    def test_empty_or_metadata_only_holdout_returns_zero(self):
        from validation.run_multi_asset_exit_audit import holdout_max_trade_count
        self.assertEqual(holdout_max_trade_count({}), 0)
        self.assertEqual(holdout_max_trade_count({"signal_rows": 4}), 0)

    def write_candles(self, interval):
        step = 3_600_000 if interval == "1h" else 14_400_000
        origin = 1_735_689_599_999
        with tempfile.NamedTemporaryFile(mode="w", newline="", suffix=".csv", delete=False, encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["timestamp", "open", "high", "low", "close"])
            writer.writeheader()
            for i in range(3):
                ms = origin + i * step
                from datetime import datetime, timezone
                stamp = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")
                writer.writerow({"timestamp": stamp, "open": 100, "high": 102, "low": 99, "close": 101})
        path = Path(f.name)
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        return path

    def test_preflight_checks_both_intervals(self):
        one = self.write_candles("1h")
        four = self.write_candles("4h")
        results, failed = check_candle_integrity(one, four)
        self.assertEqual(set(results), {"1h", "4h"})
        self.assertEqual(failed, [])

    def test_preflight_blocks_bad_four_hour_data(self):
        one = self.write_candles("1h")
        four = self.write_candles("4h")
        content = four.read_text(encoding="utf-8")
        lines = content.splitlines()
        lines[2] = lines[2].replace("2025", "2025", 1)
        lines[2] = lines[2].replace("T00:", "T03:")
        four.write_text("\n".join(lines) + "\n", encoding="utf-8")
        results, failed = check_candle_integrity(one, four)
        self.assertIn("4h", failed)
        self.assertFalse(results["4h"]["pass_basic_integrity"])


if __name__ == "__main__":
    unittest.main()
