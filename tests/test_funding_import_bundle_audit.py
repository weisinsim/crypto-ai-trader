import hashlib
import unittest

from research.funding_import_bundle_audit import audit_funding_import_bundle


class FundingImportBundleAuditTests(unittest.TestCase):
    def pages(self):
        return [
            {"page_index": 0, "request_cursor": "start", "next_cursor": "c2",
             "raw_payload": b'{"p":1}', "events": [
                 {"symbol": "BTCUSDT", "funding_timestamp_ms": 1000, "funding_rate": 0.001}]},
            {"page_index": 1, "request_cursor": "c2", "next_cursor": None,
             "raw_payload": b'{"p":2}', "events": [
                 {"symbol": "BTCUSDT", "funding_timestamp_ms": 2000, "funding_rate": -0.001}]},
        ]

    def manifest(self, pages=None, **overrides):
        pages = self.pages() if pages is None else pages
        raw_manifest_payload = b'{"manifest":"bundle-test"}'
        digests = [hashlib.sha256(p["raw_payload"]).hexdigest() for p in pages]
        aggregate = hashlib.sha256("".join(digests).encode("ascii")).hexdigest()
        value = {
            "venue": "binance", "symbol": "BTCUSDT",
            "contract_type": "linear_perpetual",
            "source_endpoint": "/fapi/v1/fundingRate",
            "timestamp_unit": "ms_utc", "requested_start_ts_ms": 1000,
            "requested_end_ts_ms": 3000, "retrieved_at_ms": 4000,
            "raw_payload_sha256": hashlib.sha256(raw_manifest_payload).hexdigest(),
            "raw_pages_aggregate_sha256": aggregate,
            "page_count": len(pages), "pagination_complete": True,
        }
        value.update(overrides)
        return value

    def audit(self, manifest=None, pages=None, raw_manifest_payload=None, parsed_raw_events_by_page=None):
        pages = self.pages() if pages is None else pages
        manifest = self.manifest(pages) if manifest is None else manifest
        return audit_funding_import_bundle(
            manifest, pages, symbol="BTCUSDT", venue="Binance",
            start_ts_ms=1000, end_ts_ms=3000, expected_first_cursor="start",
            raw_manifest_payload=raw_manifest_payload,
            parsed_raw_events_by_page=parsed_raw_events_by_page,
        )

    def test_valid_bundle_passes_internal_consistency_only(self):
        result = self.audit()
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["page_count_matches_manifest"])
        self.assertTrue(result["aggregate_digest_present"])
        self.assertTrue(result["aggregate_digest_verified"])
        self.assertFalse(result["raw_digest_verified"])
        self.assertFalse(result["normalization_verified"])
        self.assertFalse(result["normalized_events_match_raw_payload"])
        self.assertIn("independently parsed raw events", result["warning"])
        self.assertFalse(result["source_verified"])
        self.assertFalse(result["event_coverage_verified"])
        self.assertFalse(result["auto_trade_authorized"])


    def test_bundle_exposes_independent_normalization_reconciliation(self):
        pages = self.pages()
        parsed = [
            [{"symbol": "BTCUSDT", "funding_timestamp_ms": 1000, "funding_rate": 0.001}],
            [{"symbol": "BTCUSDT", "funding_timestamp_ms": 2000, "funding_rate": -0.001}],
        ]
        result = self.audit(parsed_raw_events_by_page=parsed)
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["normalization_verified"])
        self.assertTrue(result["normalized_events_match_raw_payload"])

        parsed[0][0]["funding_rate"] = 0.9
        result = self.audit(parsed_raw_events_by_page=parsed)
        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(result["normalization_verified"])

    def test_exact_raw_manifest_payload_is_verified_when_supplied(self):
        payload = b'{"manifest":"bundle-test"}'
        result = self.audit(raw_manifest_payload=payload)
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["raw_digest_verified"])
        self.assertEqual(result["raw_digest_audit"]["status"], "PASS")

    def test_changed_raw_manifest_payload_fails(self):
        payload = b'{"manifest":"bundle-test"} '
        result = self.audit(raw_manifest_payload=payload)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("raw_payload_digest_failed", result["errors"])
        self.assertFalse(result["raw_digest_verified"])

    def test_manifest_page_count_mismatch_fails(self):
        result = self.audit(self.manifest(page_count=3))
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("manifest_page_count_mismatch", result["errors"])

    def test_aggregate_digest_mismatch_fails(self):
        result = self.audit(self.manifest(raw_pages_aggregate_sha256="0" * 64))
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("manifest_aggregate_digest_mismatch", result["errors"])

    def test_page_failure_propagates(self):
        pages = self.pages()
        pages[1]["request_cursor"] = "broken"
        result = self.audit(self.manifest(pages), pages)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("page_audit_failed", result["errors"])

    def test_aggregate_digest_is_optional_but_not_claimed_as_verified(self):
        manifest = self.manifest()
        del manifest["raw_pages_aggregate_sha256"]
        result = self.audit(manifest)
        self.assertEqual(result["status"], "PASS")
        self.assertFalse(result["aggregate_digest_present"])
        self.assertFalse(result["aggregate_digest_verified"])
        self.assertFalse(result["raw_digest_verified"])


if __name__ == "__main__":
    unittest.main()
