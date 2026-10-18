import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research.audit_funding_data import audit_funding_against_candles, main


class FundingAuditTests(unittest.TestCase):
    def setUp(self):
        self.candles = [{"t": 100}, {"t": 200}, {"t": 300}, {"t": 400}]
        self.funding = [
            {"funding_time": 100, "funding_rate": 0.001, "mark_price": 100,
             "symbol": "BTCUSDT", "mark_price_source": "reported", "venue": "binance_usdm"},
            {"funding_time": 250, "funding_rate": 0.001, "mark_price": 101,
             "symbol": "BTCUSDT", "mark_price_source": "reported", "venue": "binance_usdm"},
            {"funding_time": 400, "funding_rate": -0.001, "mark_price": 99,
             "symbol": "BTCUSDT", "mark_price_source": "reported", "venue": "binance_usdm"},
        ]

    def test_nonchronological_candle_rows_require_review(self):
        candles = [self.candles[0], self.candles[2], self.candles[1], self.candles[3]]
        report = audit_funding_against_candles(candles, self.funding, "BTCUSDT")
        self.assertFalse(report["candle_timestamps_strictly_increasing"])
        self.assertEqual(report["status"], "REVIEW_REQUIRED")

    def test_duplicate_candle_timestamps_require_review(self):
        candles = self.candles + [{"t": 200}]
        report = audit_funding_against_candles(candles, self.funding, "BTCUSDT")
        self.assertFalse(report["candle_timestamps_strictly_increasing"])
        self.assertEqual(report["duplicate_candle_timestamps"], [200])
        self.assertEqual(report["duplicate_candle_records"], 1)
        self.assertEqual(report["status"], "REVIEW_REQUIRED")
        self.assertEqual(report["candle_range"]["count"], 5)

    def test_reports_nonmatching_timestamp_without_snapping(self):
        report = audit_funding_against_candles(self.candles, self.funding, "btcusdt")
        self.assertEqual(report["status"], "REVIEW_REQUIRED")
        self.assertEqual(report["funding"]["exact_candle_open_matches"], 2)
        self.assertEqual(report["funding"]["nonmatching_in_range"], 1)
        self.assertEqual(report["funding"]["records_outside_candle_range"], 0)
        self.assertFalse(report["event_completeness_verified"])
        self.assertIn("without snapping arbitrary times", report["note"])

    def test_audit_reports_raw_jitter_and_normalized_alignment_separately(self):
        rows = [dict(self.funding[0]), dict(self.funding[-1])]
        rows[0]["funding_time"] = 100
        rows[0]["funding_time_raw"] = 101
        rows[1]["funding_time_raw"] = 400
        report = audit_funding_against_candles(self.candles, rows, "BTCUSDT")
        self.assertEqual(report["funding"]["exact_candle_open_matches"], 1)
        self.assertEqual(report["funding"]["normalized_candle_open_matches"], 2)
        self.assertEqual(report["funding"]["boundary_jitter_normalized_records"], 1)
        self.assertEqual(report["funding"]["nonmatching_in_range"], 0)
        self.assertEqual(report["funding"]["first_event_ts"], 101)
        self.assertEqual(report["status"], "BOUNDARY_JITTER_NORMALIZED_ALIGNMENT_PASS_NOT_RECONCILED")

    def test_gap_intervals_are_computed_in_raw_timestamp_order(self):
        rows = [dict(self.funding[0]), dict(self.funding[-1])]
        rows[0]["funding_time"] = 300
        rows[0]["funding_time_raw"] = 300
        rows[1]["funding_time"] = 400
        rows[1]["funding_time_raw"] = 200
        report = audit_funding_against_candles(self.candles, rows, "BTCUSDT")
        self.assertEqual(report["funding"]["first_event_ts"], 200)
        self.assertEqual(report["funding"]["last_event_ts"], 300)
        self.assertEqual(report["funding"]["event_interval_ms"]["min"], 100)
        self.assertEqual(report["funding"]["event_interval_ms"]["max"], 100)

    def test_reports_out_of_range_events(self):
        rows = [dict(self.funding[0]), dict(self.funding[-1])]
        rows[0]["funding_time"] = 50
        report = audit_funding_against_candles(self.candles, rows, "BTCUSDT")
        self.assertEqual(report["funding"]["records_outside_candle_range"], 1)
        self.assertEqual(report["status"], "REVIEW_REQUIRED")

    def test_duplicate_raw_event_timestamps_require_review(self):
        rows = [dict(self.funding[0]), dict(self.funding[-1])]
        rows[0]["funding_time_raw"] = 200
        rows[1]["funding_time_raw"] = 200
        report = audit_funding_against_candles(self.candles, rows, "BTCUSDT")
        self.assertEqual(report["duplicate_event_records"], 1)
        self.assertEqual(report["duplicate_event_timestamps"], [200])
        self.assertEqual(report["funding"]["event_interval_ms"]["min"], 0)
        self.assertEqual(report["status"], "REVIEW_REQUIRED")

    def test_exact_alignment_does_not_claim_reconciliation(self):
        rows = [dict(self.funding[0]), dict(self.funding[-1])]
        report = audit_funding_against_candles(self.candles, rows, "BTCUSDT")
        self.assertEqual(report["status"], "TIMESTAMP_ALIGNMENT_PASS_NOT_RECONCILED")
        self.assertFalse(report["source_authenticity_verified"])
        self.assertFalse(report["account_cashflows_reconciled"])

    def test_optional_gap_threshold_flags_large_intervals_for_review(self):
        rows = [dict(self.funding[0]), dict(self.funding[-1])]
        rows[0]["funding_time"] = 100
        rows[1]["funding_time"] = 400
        report = audit_funding_against_candles(self.candles, rows, "BTCUSDT", max_gap_hours=0.00005)
        self.assertEqual(report["status"], "REVIEW_REQUIRED")
        self.assertEqual(report["intervals_exceeding_gap_threshold"], 1)
        self.assertFalse(report["event_completeness_verified"])

    def test_gap_threshold_also_reviews_long_leading_and_trailing_range_edges(self):
        rows = [dict(self.funding[0]), dict(self.funding[-1])]
        rows[0]["funding_time"] = 200
        rows[0]["funding_time_raw"] = 200
        rows[1]["funding_time"] = 300
        rows[1]["funding_time_raw"] = 300
        report = audit_funding_against_candles(self.candles, rows, "BTCUSDT", max_gap_hours=0.00001)
        edge_gaps = report["funding"]["event_interval_ms"]["range_edge_gaps_ms"]
        self.assertEqual(edge_gaps["leading_from_first_candle_to_first_funding_event"], 100)
        self.assertEqual(edge_gaps["trailing_from_last_funding_event_to_last_candle"], 100)
        self.assertTrue(edge_gaps["edge_gaps_exceeding_threshold"])
        self.assertEqual(report["status"], "REVIEW_REQUIRED")
        self.assertFalse(report["event_completeness_verified"])

    def test_no_in_range_funding_event_requires_edge_review_when_threshold_is_set(self):
        rows = [dict(self.funding[0])]
        rows[0]["funding_time"] = 50
        rows[0]["funding_time_raw"] = 50
        report = audit_funding_against_candles(self.candles, rows, "BTCUSDT", max_gap_hours=1)
        self.assertTrue(report["funding"]["event_interval_ms"]["range_edge_gaps_ms"]["edge_gaps_exceeding_threshold"])
        self.assertEqual(report["status"], "REVIEW_REQUIRED")

    def test_gap_threshold_must_be_positive(self):
        with self.assertRaisesRegex(ValueError, "finite positive number"):
            audit_funding_against_candles(self.candles, self.funding, "BTCUSDT", max_gap_hours=0)

    def test_gap_threshold_rejects_non_finite_values_and_booleans(self):
        for value in (float("nan"), float("inf"), float("-inf"), True):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "finite positive number"):
                    audit_funding_against_candles(
                        self.candles, self.funding, "BTCUSDT", max_gap_hours=value
                    )

    def test_malformed_symbol_values_fail_closed_without_attribute_error(self):
        rows = [dict(self.funding[0]), dict(self.funding[-1])]
        rows[0]["symbol"] = None
        with self.assertRaisesRegex(ValueError, "do not match"):
            audit_funding_against_candles(self.candles, rows, "BTCUSDT")

    def test_mixed_null_and_non_text_provenance_labels_are_safe(self):
        rows = [dict(self.funding[0]), dict(self.funding[-1])]
        rows[0]["mark_price_source"] = None
        rows[1]["mark_price_source"] = 7
        rows[0]["venue"] = None
        rows[1]["venue"] = {"unexpected": "object"}
        report = audit_funding_against_candles(self.candles, rows, "BTCUSDT")
        self.assertEqual(report["funding"]["source_labels"], ["invalid:int", "unspecified"])
        self.assertEqual(report["funding"]["venue_labels"], ["invalid:dict", "unspecified"])
        self.assertFalse(report["source_authenticity_verified"])
        self.assertEqual(report["funding"]["records_missing_or_invalid_source_label"], 2)
        self.assertEqual(report["funding"]["records_missing_or_invalid_venue_label"], 2)
        self.assertEqual(report["status"], "REVIEW_REQUIRED")

    def test_missing_or_blank_provenance_labels_require_review(self):
        rows = [dict(self.funding[0]), dict(self.funding[-1])]
        rows[0].pop("mark_price_source")
        rows[1]["mark_price_source"] = "  "
        rows[0]["venue"] = None
        rows[1]["venue"] = "binance_usdm"
        report = audit_funding_against_candles(self.candles, rows, "BTCUSDT")
        self.assertEqual(report["funding"]["records_missing_or_invalid_source_label"], 2)
        self.assertEqual(report["funding"]["records_missing_or_invalid_venue_label"], 1)
        self.assertEqual(report["status"], "REVIEW_REQUIRED")
        self.assertFalse(report["source_authenticity_verified"])

    def test_complete_text_provenance_labels_do_not_create_review_by_themselves(self):
        report = audit_funding_against_candles(self.candles, self.funding[:1] + self.funding[-1:], "BTCUSDT")
        self.assertEqual(report["funding"]["records_missing_or_invalid_source_label"], 0)
        self.assertEqual(report["funding"]["records_missing_or_invalid_venue_label"], 0)
        self.assertEqual(report["status"], "TIMESTAMP_ALIGNMENT_PASS_NOT_RECONCILED")

    def test_cli_report_fingerprints_exact_input_bytes_without_authenticity_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            candles_path = Path(tmp) / "candles.csv"
            funding_path = Path(tmp) / "funding.csv"
            output_path = Path(tmp) / "audit.json"
            candles_path.write_text(
                "t,o,h,l,c,v,q\n0,100,101,99,100,1,100\n3600000,100,101,99,100,1,100\n",
                encoding="utf-8",
            )
            funding_path.write_text(
                "symbol,funding_time,funding_rate,mark_price,mark_price_source,venue\n"
                "BTCUSDT,0,0.001,100,reported,binance_usdm\n"
                "BTCUSDT,3600000,0.001,100,reported,binance_usdm\n",
                encoding="utf-8",
            )
            argv = [
                "audit_funding_data.py", "--candles", str(candles_path),
                "--funding-csv", str(funding_path), "--symbol", "BTCUSDT",
                "--interval", "1h", "--output", str(output_path),
            ]
            with patch.object(sys, "argv", argv):
                main()
            report = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(
                report["input_manifest"]["candles"]["sha256"],
                hashlib.sha256(candles_path.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                report["input_manifest"]["funding"]["sha256"],
                hashlib.sha256(funding_path.read_bytes()).hexdigest(),
            )
            self.assertFalse(report["input_manifest"]["source_authenticity_verified"])
            self.assertFalse(report["input_manifest"]["candles"]["source_authenticity_verified"])

    def test_symbol_mismatch_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "do not match"):
            audit_funding_against_candles(self.candles, self.funding, "ETHUSDT")


if __name__ == "__main__":
    unittest.main()
