"""Join funding import metadata to the per-page pagination audit."""
from __future__ import annotations

from research.funding_import_manifest import audit_funding_import_manifest
from research.funding_pagination_audit import audit_funding_pages


def audit_funding_import_bundle(manifest, pages, *, symbol, venue,
                                start_ts_ms, end_ts_ms,
                                expected_first_cursor=None):
    """Fail closed unless metadata and page-level checks agree.

    This combines internal consistency checks only. It does not authenticate the
    venue, prove the endpoint's semantics, or independently establish that the
    exchange returned every expected settlement.
    """
    metadata = audit_funding_import_manifest(
        manifest, symbol=symbol, venue=venue,
        start_ts_ms=start_ts_ms, end_ts_ms=end_ts_ms,
    )
    page_audit = audit_funding_pages(
        pages, symbol=symbol, start_ts_ms=start_ts_ms, end_ts_ms=end_ts_ms,
        expected_first_cursor=expected_first_cursor,
    )
    errors = []
    if metadata["status"] != "PASS":
        errors.append("manifest_metadata_invalid")
    if page_audit["status"] != "PASS":
        errors.append("page_audit_failed")
    expected_count = manifest.get("page_count") if isinstance(manifest, dict) else None
    if isinstance(expected_count, bool) or not isinstance(expected_count, int) or expected_count != page_audit["page_count"]:
        errors.append("manifest_page_count_mismatch")
    if isinstance(manifest, dict):
        claimed_digest = manifest.get("raw_payload_sha256")
        page_digests = [p["raw_payload_sha256"] for p in page_audit["page_reports"]]
        if all(isinstance(d, str) for d in page_digests):
            # The manifest contract defines the aggregate digest as SHA-256 over
            # the concatenated lowercase page-digest hex strings, in page order.
            import hashlib
            aggregate = hashlib.sha256("".join(page_digests).encode("ascii")).hexdigest()
            if not isinstance(claimed_digest, str) or aggregate.lower() != claimed_digest.lower():
                errors.append("manifest_aggregate_digest_mismatch")
        else:
            errors.append("page_digest_unavailable")
    return {
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "metadata_audit": metadata,
        "page_audit": page_audit,
        "page_count_matches_manifest": "manifest_page_count_mismatch" not in errors,
        "aggregate_digest_verified": "manifest_aggregate_digest_mismatch" not in errors and "page_digest_unavailable" not in errors,
        "source_verified": False,
        "event_coverage_verified": False,
        "auto_trade_authorized": False,
        "warning": "Bundle checks compare supplied metadata and pages; they do not authenticate exchange provenance or prove the source API returned every event.",
    }
