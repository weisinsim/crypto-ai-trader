import hashlib
import unittest

from research.funding_import_manifest import (
    audit_funding_import_manifest,
    verify_raw_payload_sha256,
)


class FundingImportManifestTests(unittest.TestCase):
    def manifest(self, **overrides):
        value = {
            "venue": "binance",
            "symbol": "BTCUSDT",
            "contract_type": "linear_perpetual",
            "source_endpoint": "/fapi/v1/fundingRate",
            "timestamp_unit": "ms_utc",
            "requested_start_ts_ms": 1000,
            "requested_end_ts_ms": 9000,
            "retrieved_at_ms": 10000,
            "raw_payload_sha256": "a" * 64,
            "page_count": 2,
            "pagination_complete": True,
        }
        value.update(overrides)
        return value

    def audit(self, manifest):
        return audit_funding_import_manifest(
            manifest, symbol="BTCUSDT", venue="Binance",
            start_ts_ms=1000, end_ts_ms=9000,
        )

    def test_valid_manifest_passes_metadata_gate_but_not_provenance(self):
        result = self.audit(self.manifest())
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["manifest_metadata_consistent"])
        self.assertFalse(result["source_verified"])
        self.assertFalse(result["raw_digest_verified"])
        self.assertFalse(result["event_coverage_verified"])
        self.assertFalse(result["auto_trade_authorized"])

    def test_rejects_wrong_symbol_venue_contract_and_timestamp_unit(self):
        result = self.audit(self.manifest(
            symbol="ETHUSDT", venue="other", contract_type="spot",
            timestamp_unit="seconds",
        ))
        self.assertEqual(result["status"], "FAIL")
        for error in ("symbol_mismatch", "venue_mismatch",
                      "unsupported_contract_type", "unsupported_timestamp_unit"):
            self.assertIn(error, result["errors"])

    def test_rejects_bad_digest_pagination_and_page_count(self):
        result = self.audit(self.manifest(
            raw_payload_sha256="not-a-digest", pagination_complete=False,
            page_count=0,
        ))
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("invalid_raw_payload_sha256", result["errors"])
        self.assertIn("pagination_not_confirmed_complete", result["errors"])
        self.assertIn("invalid_page_count", result["errors"])

    def test_rejects_requested_window_mismatch(self):
        result = self.audit(self.manifest(
            requested_start_ts_ms=999, requested_end_ts_ms=9001,
        ))
        self.assertIn("requested_start_mismatch", result["errors"])
        self.assertIn("requested_end_mismatch", result["errors"])

    def test_malformed_field_types_fail_closed_without_crashing(self):
        result = self.audit(self.manifest(
            symbol=123, venue=None, retrieved_at_ms=True,
            page_count=True, raw_payload_sha256=123,
        ))
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("symbol_mismatch", result["errors"])
        self.assertIn("venue_mismatch", result["errors"])
        self.assertIn("invalid_retrieved_at_ms", result["errors"])
        self.assertIn("invalid_page_count", result["errors"])
        self.assertIn("invalid_raw_payload_sha256", result["errors"])

    def test_rejects_missing_fields_and_incomplete_pagination(self):
        manifest = self.manifest()
        del manifest["source_endpoint"]
        del manifest["requested_start_ts_ms"]
        result = self.audit(manifest)
        self.assertIn("missing_or_invalid:source_endpoint", result["errors"])
        self.assertIn("missing_or_invalid:requested_start_ts_ms", result["errors"])

    def test_verifies_digest_of_exact_raw_bytes(self):
        payload = b'{"symbol":"BTCUSDT","rate":"0.0001"}'
        manifest = {"raw_payload_sha256": hashlib.sha256(payload).hexdigest()}
        result = verify_raw_payload_sha256(manifest, payload)
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["raw_digest_verified"])
        self.assertFalse(result["source_verified"])
        self.assertFalse(result["event_coverage_verified"])
        self.assertFalse(result["auto_trade_authorized"])

    def test_digest_verification_rejects_changed_bytes_and_invalid_claim(self):
        payload = b'{"rate":"0.0001"}'
        result = verify_raw_payload_sha256(
            {"raw_payload_sha256": hashlib.sha256(payload).hexdigest()},
            payload + b" ",
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["error"], "raw_payload_digest_mismatch")
        invalid = verify_raw_payload_sha256({"raw_payload_sha256": "bad"}, payload)
        self.assertEqual(invalid["error"], "invalid_raw_payload_sha256")

    def test_digest_verification_requires_bytes_not_parsed_objects(self):
        with self.assertRaises(ValueError):
            verify_raw_payload_sha256({"raw_payload_sha256": "a" * 64}, "payload")


if __name__ == "__main__":
    unittest.main()
