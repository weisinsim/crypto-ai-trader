"""Strict metadata gate for funding-history imports (research-only).

This validates import-manifest shape and consistency and can verify a digest
against caller-supplied exact raw bytes. It does not contact an exchange or
prove source authenticity or event coverage.
"""
from __future__ import annotations

import hashlib
import re


_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def verify_raw_payload_sha256(manifest, raw_payload):
    """Compare the manifest SHA-256 with the exact raw response bytes.

    The caller is responsible for passing the unmodified bytes as received,
    not parsed/re-serialized JSON. This proves byte-level digest agreement only;
    it does not prove where those bytes originated or that all pages were fetched.
    """
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be a mapping")
    if not isinstance(raw_payload, bytes):
        raise ValueError("raw_payload must be bytes from the unmodified response")
    claimed = manifest.get("raw_payload_sha256")
    if not isinstance(claimed, str) or not _SHA256_RE.fullmatch(claimed):
        return {
            "status": "FAIL",
            "error": "invalid_raw_payload_sha256",
            "computed_sha256": hashlib.sha256(raw_payload).hexdigest(),
            "raw_digest_verified": False,
            "source_verified": False,
            "event_coverage_verified": False,
            "auto_trade_authorized": False,
        }
    computed = hashlib.sha256(raw_payload).hexdigest()
    matches = computed.lower() == claimed.lower()
    return {
        "status": "PASS" if matches else "FAIL",
        "error": None if matches else "raw_payload_digest_mismatch",
        "computed_sha256": computed,
        "raw_digest_verified": matches,
        "source_verified": False,
        "event_coverage_verified": False,
        "auto_trade_authorized": False,
        "warning": "Digest agreement verifies bytes only, not exchange provenance or pagination completeness.",
    }


def audit_funding_import_manifest(
    manifest, *, symbol, venue, start_ts_ms, end_ts_ms,
):
    """Validate provenance metadata before funding events can be considered.

    The caller must separately verify the digest against exact raw bytes and
    reconcile every page with the source API. PASS means metadata is internally
    consistent, not that exchange provenance or event completeness is proved.
    """
    errors = []
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be a mapping")
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("symbol must be a non-empty string")
    if not isinstance(venue, str) or not venue.strip():
        raise ValueError("venue must be a non-empty string")
    for name, value in (("start_ts_ms", start_ts_ms), ("end_ts_ms", end_ts_ms)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a nonnegative integer timestamp")
    if end_ts_ms <= start_ts_ms:
        raise ValueError("end_ts_ms must be greater than start_ts_ms")

    required_text = ("venue", "symbol", "contract_type", "source_endpoint", "timestamp_unit")
    for field in required_text:
        value = manifest.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"missing_or_invalid:{field}")

    if isinstance(manifest.get("requested_start_ts_ms"), bool) or not isinstance(manifest.get("requested_start_ts_ms"), int):
        errors.append("missing_or_invalid:requested_start_ts_ms")
    elif manifest["requested_start_ts_ms"] != start_ts_ms:
        errors.append("requested_start_mismatch")

    if isinstance(manifest.get("requested_end_ts_ms"), bool) or not isinstance(manifest.get("requested_end_ts_ms"), int):
        errors.append("missing_or_invalid:requested_end_ts_ms")
    elif manifest["requested_end_ts_ms"] != end_ts_ms:
        errors.append("requested_end_mismatch")

    manifest_symbol = manifest.get("symbol")
    if not isinstance(manifest_symbol, str) or manifest_symbol.strip().upper() != symbol.strip().upper():
        errors.append("symbol_mismatch")
    manifest_venue = manifest.get("venue")
    if not isinstance(manifest_venue, str) or manifest_venue.strip().lower() != venue.strip().lower():
        errors.append("venue_mismatch")
    if manifest.get("contract_type") != "linear_perpetual":
        errors.append("unsupported_contract_type")
    if manifest.get("timestamp_unit") != "ms_utc":
        errors.append("unsupported_timestamp_unit")

    digest = manifest.get("raw_payload_sha256")
    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
        errors.append("invalid_raw_payload_sha256")

    retrieved = manifest.get("retrieved_at_ms")
    if isinstance(retrieved, bool) or not isinstance(retrieved, int) or retrieved < end_ts_ms:
        errors.append("invalid_retrieved_at_ms")

    page_count = manifest.get("page_count")
    if isinstance(page_count, bool) or not isinstance(page_count, int) or page_count < 1:
        errors.append("invalid_page_count")
    if manifest.get("pagination_complete") is not True:
        errors.append("pagination_not_confirmed_complete")

    return {
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "symbol": symbol.strip().upper(),
        "venue": venue.strip(),
        "requested_start_ts_ms": start_ts_ms,
        "requested_end_ts_ms": end_ts_ms,
        "raw_payload_sha256_format_valid": isinstance(digest, str) and bool(_SHA256_RE.fullmatch(digest)),
        "manifest_metadata_consistent": not errors,
        "source_verified": False,
        "raw_digest_verified": False,
        "event_coverage_verified": False,
        "auto_trade_authorized": False,
        "warning": "Metadata validation only; independently verify raw bytes and reconcile exchange pagination, contract identity, and event coverage.",
    }
