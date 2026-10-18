import unittest

from research.build_research_manifest import build_manifest


class ResearchManifestTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {
                "symbol": symbol,
                "candle_status": "COMPLETE",
                "funding_download_status": "DOWNLOADED_NOT_RECONCILED",
                "funding_audit_status": "TIMESTAMP_ALIGNMENT_PASS_NOT_RECONCILED",
                "baseline_status": "RESEARCH_ONLY",
            }
            for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT", "XRPUSDT")
        ]

    def test_complete_batch_is_never_promoted_to_profitability_claim(self):
        result = build_manifest(self.rows)
        self.assertEqual(result["batch_completion_status"], "ALL_ASSETS_PROCESSED_NOT_RECONCILED")
        self.assertEqual(result["funding_data_quality_status"], "TIMESTAMP_ALIGNMENT_ONLY_NOT_RECONCILED")
        self.assertTrue(result["symbol_set_complete"])
        self.assertTrue(result["processing_statuses_complete"])
        self.assertFalse(result["profitability_claim_eligible"])
        self.assertFalse(result["strategy_approval"])
        self.assertFalse(result["live_trading_enabled"])

    def test_missing_or_duplicate_symbol_fails_closed(self):
        for rows in (self.rows[:-1], self.rows[:-1] + [dict(self.rows[0])]):
            with self.subTest(rows=rows):
                result = build_manifest(rows)
                self.assertEqual(result["batch_completion_status"], "PARTIAL_OR_FAILED")
                self.assertFalse(result["symbol_set_complete"])

    def test_unknown_audit_status_requires_review(self):
        rows = [dict(row) for row in self.rows]
        rows[0]["funding_audit_status"] = "SOMETHING_NEW"
        result = build_manifest(rows)
        self.assertEqual(result["batch_completion_status"], "ALL_ASSETS_PROCESSED_REVIEW_REQUIRED")
        self.assertEqual(result["funding_data_quality_status"], "FUNDING_AUDIT_REVIEW_REQUIRED")
        self.assertTrue(result["funding_audit_review_required"])

    def test_failed_or_unreadable_audit_stage_marks_batch_incomplete(self):
        for status in ("FAILED", "ERROR", "UNKNOWN", ""):
            rows = [dict(row) for row in self.rows]
            rows[0]["funding_audit_status"] = status
            result = build_manifest(rows)
            with self.subTest(status=status):
                self.assertEqual(result["batch_completion_status"], "PARTIAL_OR_FAILED")
                self.assertEqual(result["funding_data_quality_status"], "BATCH_INCOMPLETE_REVIEW_REQUIRED")
                self.assertFalse(result["processing_statuses_complete"])
                self.assertFalse(result["live_trading_enabled"])

    def test_proxy_marks_always_require_settlement_review(self):
        rows = [dict(row) for row in self.rows]
        rows[0]["funding_download_status"] = "DOWNLOADED_PROXY_MARK_PRICE_NOT_RECONCILED"
        result = build_manifest(rows)
        self.assertEqual(result["funding_data_quality_status"], "PROXY_MARKS_NOT_SETTLEMENT_REVIEW_REQUIRED")
        self.assertTrue(result["funding_proxy_review_required"])
        self.assertFalse(result["profitability_claim_eligible"])

    def test_failed_candle_or_baseline_status_fails_closed(self):
        for field, value in (
            ("candle_status", "FAILED"),
            ("baseline_status", "UNKNOWN"),
            ("funding_download_status", "FAILED"),
        ):
            rows = [dict(row) for row in self.rows]
            rows[0][field] = value
            result = build_manifest(rows)
            with self.subTest(field=field):
                self.assertEqual(result["batch_completion_status"], "PARTIAL_OR_FAILED")
                self.assertFalse(result["processing_statuses_complete"])

    def test_malformed_null_or_non_text_fields_fail_closed_without_crashing(self):
        rows = [dict(row) for row in self.rows]
        rows[0]["symbol"] = None
        rows[1]["candle_status"] = None
        rows[2]["funding_download_status"] = None
        rows[3]["funding_audit_status"] = None
        rows[4]["baseline_status"] = 123
        result = build_manifest(rows)
        self.assertEqual(result["batch_completion_status"], "PARTIAL_OR_FAILED")
        self.assertEqual(result["funding_data_quality_status"], "BATCH_INCOMPLETE_REVIEW_REQUIRED")
        self.assertTrue(result["funding_audit_review_required"])
        self.assertFalse(result["profitability_claim_eligible"])
        self.assertFalse(result["live_trading_enabled"])

    def test_missing_rows_is_partial_and_not_live(self):
        result = build_manifest([])
        self.assertEqual(result["batch_completion_status"], "PARTIAL_OR_FAILED")
        self.assertFalse(result["processing_statuses_complete"])
        self.assertEqual(result["funding_data_quality_status"], "BATCH_INCOMPLETE_REVIEW_REQUIRED")
        self.assertFalse(result["live_trading_enabled"])


if __name__ == "__main__":
    unittest.main()
