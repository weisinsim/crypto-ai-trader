"""Join funding import metadata to the per-page pagination audit."""
from __future__ import annotations

import hashlib

from research.funding_import_manifest import (
    audit_funding_import_manifest,
    verify_raw_payload_sha256,
)
from research.funding_pagination_audit import audit_funding_pages


def audit_funding_import_bundle(manifest, pages, *, symbol, venue,
                                start_ts_ms, end_ts_ms,
                                expected_first_cursor=None,
                                raw_manifest_payload=None):
    """Fail closed unless metadata and page-level checks agree.

    ``raw_payload_sha256`` retains its original meaning: digest of the exact
    raw payload represented by the manifest. Pass ``raw_manifest_payload``
    to verify those exact bytes. The optional ``raw_pages_aggregate_sha256``
    is a separate bundle digest over concatenated per-page SHA-256 hex strings
    in page order.

    PASS means supplied metadata and envelope checks agree. It does not prove
    that normalized events were parsed faithfully from raw page bytes.
    """
    metadata = audit_funding_import_manifest(
        manifest, symbol=symbol, venue=venue,
        start_ts_ms=start_ts_ms, end_ts_ms=end_ts_ms,
    )
    page_audit = audit_funding_pages(
        pages, symbol=symbol, start_ts_ms=start_ts_ms, end_ts_ms=end_ts_ms,
        expected_first_cursor=expected_first_cursor,
    )
    raw_digest_audit = None
    if raw_manifest_payload is not None:
        raw_digest_audit = verify_raw_payload_sha256(manifest, raw_manifest_payload)

    errors = []
    if metadata["status"] != "PASS":
        errors.append("manifest_metadata_invalid")
    if page_audit["status"] != "PASS":
        errors.append("page_audit_failed")
    if raw_digest_audit is not None and raw_digest_audit["status"] != "PASS":
        errors.append("raw_payload_digest_failed")

    expected_count = manifest.get("page_count") if isinstance(manifest, dict) else None
    if isinstance(expected_count, bool) or not isinstance(expected_count, int) or expected_count != page_audit["page_count"]:
        errors.append("manifest_page_count_mismatch")

    aggregate_verified = False
    if isinstance(manifest, dict):
        claimed_aggregate = manifest.get("raw_pages_aggregate_sha256")
        page_digests = [p["raw_payload_sha256"] for p in page_audit["page_reports"]]
        if claimed_aggregate is not None:
            if all(isinstance(d, str) for d in page_digests):
                aggregate = hashlib.sha256("".join(page_digests).encode("ascii")).hexdigest()
                aggregate_verified = isinstance(claimed_aggregate, str) and aggregate == claimed_aggregate.lower()
                if not aggregate_verified:
                    errors.append("manifest_aggregate_digest_mismatch")
            else:
                errors.append("page_digest_unavailable")

    return {
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "metadata_audit": metadata,
        "raw_digest_audit": raw_digest_audit,
        "page_audit": page_audit,
        "page_count_matches_manifest": "manifest_page_count_mismatch" not in errors,
        "aggregate_digest_present": isinstance(manifest, dict) and manifest.get("raw_pages_aggregate_sha256") is not None,
        "aggregate_digest_verified": aggregate_verified,
        "raw_digest_verified": raw_digest_audit is not None and raw_digest_audit["status"] == "PASS",
        "normalization_verified": False,
        "normalized_events_match_raw_payload": False,
        "source_verified": False,
        "event_coverage_verified": False,
        "auto_trade_authorized": False,
        "warning": "PASS means metadata and supplied envelopes are internally consistent only. Raw page bytes are hashed but not parsed or reconciled against normalized events; exchange provenance and API event completeness remain unverified.",
    }
