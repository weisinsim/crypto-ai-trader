"""Build a conservative manifest for the five-asset research-only batch."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

EXPECTED_SYMBOLS = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT", "XRPUSDT"}
PASS_AUDIT_STATUSES = {
    "TIMESTAMP_ALIGNMENT_PASS_NOT_RECONCILED",
    "BOUNDARY_JITTER_NORMALIZED_ALIGNMENT_PASS_NOT_RECONCILED",
}


def build_manifest(rows: list[dict]) -> dict:
    """Summarize batch completion while failing closed on missing/unknown audit states."""
    symbols = [row.get("symbol", "").strip().upper() for row in rows]
    symbols_unique = len(symbols) == len(set(symbols))
    symbol_set_complete = len(rows) == len(EXPECTED_SYMBOLS) and set(symbols) == EXPECTED_SYMBOLS and symbols_unique
    processing_statuses_complete = all(
        row.get("candle_status") == "COMPLETE"
        and row.get("funding_download_status") in {
            "DOWNLOADED_NOT_RECONCILED",
            "DOWNLOADED_PROXY_MARK_PRICE_NOT_RECONCILED",
        }
        and row.get("baseline_status") == "RESEARCH_ONLY"
        for row in rows
    )
    all_assets_processed = symbol_set_complete and processing_statuses_complete
    audit_statuses = [row.get("funding_audit_status", "UNKNOWN") for row in rows]
    audit_review = any(status not in PASS_AUDIT_STATUSES for status in audit_statuses)
    any_proxy = any(
        "PROXY" in row.get("funding_download_status", "").upper()
        for row in rows
    )
    if not all_assets_processed:
        completion = "PARTIAL_OR_FAILED"
    elif audit_review:
        completion = "ALL_ASSETS_PROCESSED_REVIEW_REQUIRED"
    else:
        completion = "ALL_ASSETS_PROCESSED_NOT_RECONCILED"
    if any_proxy:
        quality = "PROXY_MARKS_NOT_SETTLEMENT_REVIEW_REQUIRED"
    elif audit_review:
        quality = "FUNDING_AUDIT_REVIEW_REQUIRED"
    else:
        quality = "TIMESTAMP_ALIGNMENT_ONLY_NOT_RECONCILED"
    return {
        "status": "RESEARCH_ONLY",
        "batch_completion_status": completion,
        "funding_data_quality_status": quality,
        "funding_proxy_review_required": any_proxy,
        "profitability_claim_eligible": False,
        "expected_assets": len(EXPECTED_SYMBOLS),
        "expected_symbols": sorted(EXPECTED_SYMBOLS),
        "unique_symbols_observed": sorted(set(symbols)),
        "symbol_set_complete": symbol_set_complete,
        "processing_statuses_complete": processing_statuses_complete,
        "strategy_approval": False,
        "live_trading_enabled": False,
        "funding_gap_review_threshold_hours": 12,
        "interpretation": (
            "EMA baseline is a data-pipeline smoke test, not the asset-adaptive dashboard model. "
            "Funding gaps exceeding 12 hours are flagged for review; this threshold is diagnostic, not proof of complete event coverage. "
            "Unknown or non-passing funding audit states require review. Funding download is not cashflow reconciliation; "
            "timestamp alignment is not proof of complete coverage. When proxy marks are used, they are not actual settlement marks. "
            "These outputs are exploratory only and are not eligible for profitability claims or strategy promotion."
        ),
        "rows_completed": len(rows),
        "funding_proxy_used": any_proxy,
        "funding_audit_review_required": audit_review,
        "assets": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    rows = []
    summary = Path(args.summary)
    if summary.exists():
        with summary.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    payload = build_manifest(rows)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
