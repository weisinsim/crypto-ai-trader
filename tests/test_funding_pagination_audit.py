import hashlib
import unittest

from research.funding_pagination_audit import audit_funding_pages


class FundingPaginationAuditTests(unittest.TestCase):
    def pages(self):
        return [
            {
                "page_index": 0,
                "request_cursor": "start",
                "next_cursor": "cursor-2",
                "raw_payload": b'{"page":1}',
                "events": [
                    {"symbol": "BTCUSDT", "funding_timestamp_ms": 1000, "funding_rate": 0.001},
                ],
            },
            {
                "page_index": 1,
                "request_cursor": "cursor-2",
                "next_cursor": None,
                "raw_payload": b'{"page":2}',
                "events": [
                    {"symbol": "BTCUSDT", "funding_timestamp_ms": 2000, "funding_rate": -0.0005},
                ],
            },
        ]

    def audit(self, pages, parsed_raw_events_by_page=None):
        return audit_funding_pages(
            pages, symbol="BTCUSDT", start_ts_ms=1000, end_ts_ms=3000,
            expected_first_cursor="start",
            parsed_raw_events_by_page=parsed_raw_events_by_page,
        )

    def test_valid_pages_compute_each_raw_digest_and_keep_provenance_unverified(self):
        result = self.audit(self.pages())
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["page_count"], 2)
        self.assertEqual(result["event_count"], 2)
        self.assertTrue(result["raw_page_digests_computed"])
        self.assertEqual(result["page_reports"][0]["raw_payload_sha256"], hashlib.sha256(b'{"page":1}').hexdigest())
        self.assertFalse(result["normalization_verified"])
        self.assertFalse(result["normalized_events_match_raw_payload"])
        self.assertIn("independently parsed raw events", result["warning"])
        self.assertFalse(result["source_verified"])
        self.assertFalse(result["event_coverage_verified"])
        self.assertFalse(result["auto_trade_authorized"])

    def test_normalized_event_mutation_is_not_misreported_as_verified(self):
        pages = self.pages()
        pages[0]["events"][0]["funding_rate"] = 0.25
        result = self.audit(pages)
        # The envelope can be internally consistent even when normalized data
        # does not match the opaque raw payload. The audit must not imply otherwise.
        self.assertEqual(result["status"], "PASS")
        self.assertFalse(result["normalization_verified"])
        self.assertFalse(result["normalized_events_match_raw_payload"])


    def test_verifies_normalization_when_independently_parsed_raw_events_match(self):
        pages = self.pages()
        parsed = [
            [{"symbol": "BTCUSDT", "funding_timestamp_ms": 1000, "funding_rate": 0.001}],
            [{"symbol": "BTCUSDT", "funding_timestamp_ms": 2000, "funding_rate": -0.0005}],
        ]
        result = self.audit(pages, parsed)
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["normalization_verified"])
        self.assertTrue(result["normalized_events_match_raw_payload"])

    def test_fails_when_normalized_events_differ_from_independent_raw_parse(self):
        pages = self.pages()
        parsed = [
            [{"symbol": "BTCUSDT", "funding_timestamp_ms": 1000, "funding_rate": 0.002}],
            [{"symbol": "BTCUSDT", "funding_timestamp_ms": 2000, "funding_rate": -0.0005}],
        ]
        result = self.audit(pages, parsed)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("page_0:normalized_events_do_not_match_raw", result["errors"])
        self.assertFalse(result["normalization_verified"])
        self.assertFalse(result["normalized_events_match_raw_payload"])

    def test_fails_when_raw_parse_page_count_does_not_match(self):
        result = self.audit(self.pages(), [])
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("normalization_page_count_mismatch", result["errors"])
        self.assertFalse(result["normalization_verified"])


    def test_invalid_normalized_row_cannot_be_marked_verified(self):
        pages = self.pages()
        pages[0]["events"][0]["funding_rate"] = float("nan")
        parsed = [
            [{"symbol": "BTCUSDT", "funding_timestamp_ms": 1000, "funding_rate": 0.001}],
            [{"symbol": "BTCUSDT", "funding_timestamp_ms": 2000, "funding_rate": -0.0005}],
        ]
        result = self.audit(pages, parsed)
        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(result["normalization_verified"])
        self.assertFalse(result["normalized_events_match_raw_payload"])

    def test_rejects_cursor_gaps_and_unfinished_final_page(self):
        pages = self.pages()
        pages[1]["request_cursor"] = "wrong-cursor"
        pages[1]["next_cursor"] = "still-more"
        result = self.audit(pages)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("page_1:cursor_chain_mismatch", result["errors"])
        self.assertIn("final_page_has_next_cursor", result["errors"])

    def test_rejects_non_advancing_and_repeated_request_cursors(self):
        pages = self.pages()
        pages[0]["next_cursor"] = "start"
        result = self.audit(pages)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("page_0:cursor_did_not_advance", result["errors"])
        pages = self.pages()
        pages[1]["request_cursor"] = "start"
        result = self.audit(pages)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("page_1:cursor_chain_mismatch", result["errors"])
        self.assertIn("page_1:repeated_request_cursor", result["errors"])

    def test_rejects_invalid_cursor_types(self):
        pages = self.pages()
        pages[0]["next_cursor"] = {"cursor": "bad"}
        result = self.audit(pages)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("page_0:invalid_next_cursor_type", result["errors"])
        pages = self.pages()
        pages[1]["request_cursor"] = ["bad"]
        result = self.audit(pages)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("page_1:invalid_request_cursor_type", result["errors"])

    def test_rejects_duplicate_and_out_of_order_events(self):
        pages = self.pages()
        pages[1]["events"] = [
            {"symbol": "BTCUSDT", "funding_timestamp_ms": 1000, "funding_rate": 0.002},
        ]
        result = self.audit(pages)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("duplicate_funding_timestamp:1000", result["errors"])

    def test_rejects_mixed_symbol_invalid_rate_and_out_of_window_event(self):
        pages = self.pages()
        pages[1]["events"] = [
            {"symbol": "ETHUSDT", "funding_timestamp_ms": 2000, "funding_rate": 0.001},
            {"symbol": "BTCUSDT", "funding_timestamp_ms": 2500, "funding_rate": float("nan")},
            {"symbol": "BTCUSDT", "funding_timestamp_ms": 5000, "funding_rate": 0.001},
        ]
        result = self.audit(pages)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("page_1:event_0_symbol_mismatch", result["errors"])
        self.assertIn("page_1:event_1_invalid_rate", result["errors"])
        self.assertIn("events_outside_requested_window", result["errors"])

    def test_extremely_large_integer_rate_fails_without_overflow_exception(self):
        pages = self.pages()
        pages[0]["events"][0]["funding_rate"] = 10 ** 10000
        result = self.audit(pages)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("page_0:event_0_invalid_rate", result["errors"])

    def test_extremely_large_integer_in_independent_parse_fails_cleanly(self):
        pages = self.pages()
        parsed = [
            [{"symbol": "BTCUSDT", "funding_timestamp_ms": 1000, "funding_rate": 10 ** 10000}],
            [{"symbol": "BTCUSDT", "funding_timestamp_ms": 2000, "funding_rate": -0.0005}],
        ]
        result = self.audit(pages, parsed)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("page_0:invalid_parsed_raw_event", result["errors"])
        self.assertFalse(result["normalization_verified"])
        self.assertFalse(result["normalized_events_match_raw_payload"])

    def test_rejects_missing_raw_bytes_and_early_pagination_end(self):
        pages = self.pages()
        pages[0]["raw_payload"] = "not bytes"
        pages[0]["next_cursor"] = None
        result = self.audit(pages)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("page_0:raw_payload_not_bytes", result["errors"])
        self.assertIn("page_0:pagination_ended_early", result["errors"])
        self.assertFalse(result["raw_page_digests_computed"])

    def test_missing_raw_bytes_prevents_normalization_verification(self):
        pages = self.pages()
        pages[0]["raw_payload"] = "not bytes"
        parsed = [
            [{"symbol": "BTCUSDT", "funding_timestamp_ms": 1000, "funding_rate": 0.001}],
            [{"symbol": "BTCUSDT", "funding_timestamp_ms": 2000, "funding_rate": -0.0005}],
        ]
        result = self.audit(pages, parsed)
        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(result["normalization_verified"])
        self.assertFalse(result["normalized_events_match_raw_payload"])

    def test_empty_raw_payload_fails_closed(self):
        pages = self.pages()
        pages[0]["raw_payload"] = b""
        parsed = [
            [{"symbol": "BTCUSDT", "funding_timestamp_ms": 1000, "funding_rate": 0.001}],
            [{"symbol": "BTCUSDT", "funding_timestamp_ms": 2000, "funding_rate": -0.0005}],
        ]
        result = self.audit(pages, parsed)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("page_0:raw_payload_empty", result["errors"])
        self.assertFalse(result["normalization_verified"])
        self.assertFalse(result["normalized_events_match_raw_payload"])

    def test_rejects_empty_pages_and_invalid_window(self):
        with self.assertRaisesRegex(ValueError, "non-empty"):
            audit_funding_pages([], symbol="BTCUSDT", start_ts_ms=0, end_ts_ms=1)
        with self.assertRaisesRegex(ValueError, "greater than"):
            audit_funding_pages(self.pages(), symbol="BTCUSDT", start_ts_ms=2, end_ts_ms=2)


if __name__ == "__main__":
    unittest.main()
