import csv
import hashlib
import tempfile
import unittest
from pathlib import Path

from research.funding_manifest import build_manifest


class FundingManifestTests(unittest.TestCase):
    def write_csv(self, folder, rows):
        path = Path(folder) / "funding.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=[
                "funding_time", "funding_rate", "mark_price", "symbol", "mark_price_source"
            ])
            writer.writeheader()
            writer.writerows(rows)
        return path

    def row(self, timestamp, source="binance_fapi_funding_rate_mark_price", symbol="BTCUSDT"):
        return {
            "funding_time": timestamp, "funding_rate": "0.0001",
            "mark_price": "100.5", "symbol": symbol, "mark_price_source": source,
        }

    def build(self, path, **overrides):
        args = {
            "symbol": "BTCUSDT", "start_ms": 100, "end_ms": 1000,
            "source_url": "https://data.binance.vision/example.zip",
            "http_status": 200, "retrieved_at": "2026-10-12T08:00:00Z",
        }
        args.update(overrides)
        return build_manifest(path, **args)

    def test_manifest_records_sha256_range_and_explicit_non_authentication(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.write_csv(folder, [self.row(200), self.row(500)])
            manifest = self.build(path)
            self.assertEqual(manifest["file"]["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(manifest["dataset"]["records"], 2)
            self.assertEqual(manifest["requested_range_ms"]["end_exclusive"], 1000)
            self.assertFalse(manifest["assessment"]["source_authenticity_verified"])
            self.assertFalse(manifest["assessment"]["event_completeness_verified"])
            self.assertEqual(manifest["assessment"]["status"], "STRUCTURAL_CHECKS_PASS")

    def test_rejects_duplicate_timestamps(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.write_csv(folder, [self.row(200), self.row(200)])
            with self.assertRaisesRegex(ValueError, "duplicate"):
                self.build(path)

    def test_rejects_wrong_symbol_and_out_of_range_event(self):
        with tempfile.TemporaryDirectory() as folder:
            wrong_symbol = self.write_csv(folder, [self.row(200, symbol="ETHUSDT")])
            with self.assertRaisesRegex(ValueError, "symbol mismatch"):
                self.build(wrong_symbol)
            out_of_range = self.write_csv(folder, [self.row(1000)])
            with self.assertRaisesRegex(ValueError, "outside requested range"):
                self.build(out_of_range)

    def test_manifest_reports_window_boundary_gaps(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.write_csv(folder, [self.row(200), self.row(900)])
            manifest = self.build(path, start_ms=100, end_ms=1000)
            dataset = manifest["dataset"]
            self.assertAlmostEqual(dataset["first_event_offset_from_start_hours"], 100 / 3_600_000)
            self.assertAlmostEqual(dataset["last_event_offset_from_end_hours"], 100 / 3_600_000)
            self.assertEqual(dataset["boundary_gaps_over_expected_interval"], 0)

    def test_proxy_prices_force_review(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.write_csv(folder, [self.row(200, source="mark_kline_1m_prior_close")])
            manifest = self.build(path)
            self.assertEqual(manifest["assessment"]["status"], "REVIEW_REQUIRED")
            self.assertEqual(manifest["dataset"]["proxy_or_prior_close_records"], 1)

    def test_rejects_non_200_source_claim_and_timezone_free_timestamp(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.write_csv(folder, [self.row(200)])
            with self.assertRaisesRegex(ValueError, "HTTP status"):
                self.build(path, http_status=404)
            with self.assertRaisesRegex(ValueError, "include a timezone"):
                self.build(path, retrieved_at="2026-10-12T08:00:00")

    def test_rejects_invalid_price_and_unlabelled_source_forces_review(self):
        with tempfile.TemporaryDirectory() as folder:
            invalid = self.row(200)
            invalid["mark_price"] = "0"
            path = self.write_csv(folder, [invalid])
            with self.assertRaisesRegex(ValueError, "invalid rate/mark price"):
                self.build(path)
            path = self.write_csv(folder, [self.row(200, source="")])
            manifest = self.build(path)
            self.assertEqual(manifest["assessment"]["status"], "REVIEW_REQUIRED")
            self.assertEqual(manifest["dataset"]["unlabelled_source_records"], 1)


if __name__ == "__main__":
    unittest.main()
