#!/usr/bin/env python3
"""Create a conservative inventory of per-asset research artifacts."""
import argparse
import json
from pathlib import Path

EXPECTED_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT", "XRPUSDT")

REQUIRED_REPORT_SUFFIXES = ("_1h_baseline.json", "_funding_audit.json")

REPORT_SUFFIXES = (
    "_1h_baseline.json", "_1h_adaptive.json", "_1h_breakout_retest.json",
    "_1h_mean_reversion.json", "_1h_frozen_stability.json",
    "_1h_avax_followup.json", "_funding_audit.json",
    "_1h_public_strategy_benchmark.json",
)


def read_payload(path):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, {"status": "INVALID_REPORT", "detail": str(exc)}
    if not isinstance(payload, dict):
        return None, {"status": "INVALID_REPORT", "detail": "JSON root is not an object"}
    return payload, None


def inspect(path):
    payload, error = read_payload(path)
    if error:
        return error
    status = payload.get("status")
    if not isinstance(status, str) or not status:
        status = payload.get("production_gate")
    if not isinstance(status, str) or not status:
        status = "REPORT_PRESENT_STATUS_UNSPECIFIED"
    return {"status": status, "detail": payload.get("reason", "")}


def funding_audit_passes(path):
    """Only an explicit status=PASS in the audit itself satisfies this gate."""
    payload, error = read_payload(path)
    return not error and payload.get("status") == "PASS"


def render(root):
    files = sorted(p for p in root.rglob("*.json") if p.name.endswith(REPORT_SUFFIXES)) if root.exists() else []
    lines = [
        "# Multi-asset research artifact inventory", "",
        "> Research-only inventory. Artifact presence or a successful workflow is not evidence of profitability or production readiness.", "",
        "| Symbol | Report | Status | Notes |", "|---|---|---|---|",
    ]
    symbols = set()
    for path in files:
        name = path.name
        symbol = name.split("_1h_", 1)[0] if "_1h_" in name else name.split("_funding_", 1)[0]
        symbols.add(symbol)
        result = inspect(path)
        report = name.replace(symbol + "_", "")
        notes = str(result["detail"]).replace("|", "\\|").replace("\n", " ")[:180]
        lines.append(f"| {symbol} | {report} | {result['status']} | {notes or '—'} |")
    missing_symbols = sorted(set(EXPECTED_SYMBOLS) - symbols)
    present_names = {path.name for path in files}
    missing_required_reports = sorted(
        f"{symbol}{suffix}"
        for symbol in EXPECTED_SYMBOLS
        for suffix in REQUIRED_REPORT_SUFFIXES
        if f"{symbol}{suffix}" not in present_names
    )
    invalid_required_reports = sorted(
        f"{symbol}{suffix}"
        for symbol in EXPECTED_SYMBOLS
        for suffix in REQUIRED_REPORT_SUFFIXES
        if f"{symbol}{suffix}" in present_names
        and inspect(next(path for path in files if path.name == f"{symbol}{suffix}"))["status"] == "INVALID_REPORT"
    )
    nonpassing_funding_audits = sorted(
        f"{symbol}_funding_audit.json"
        for symbol in EXPECTED_SYMBOLS
        if f"{symbol}_funding_audit.json" in present_names
        and not funding_audit_passes(next(path for path in files if path.name == f"{symbol}_funding_audit.json"))
    )
    coverage_status = "PASS_COVERAGE_ONLY" if not missing_required_reports and not invalid_required_reports and not nonpassing_funding_audits and not missing_symbols else "INCOMPLETE_REPORT_COVERAGE"
    lines.extend(["", "## Coverage", "",
                  f"- Coverage status: {coverage_status}",
                  f"- Expected symbols: {len(EXPECTED_SYMBOLS)} ({', '.join(EXPECTED_SYMBOLS)})",
                  f"- Symbols represented: {len(symbols)} ({', '.join(sorted(symbols)) or 'none'})",
                  f"- Missing expected symbols: {', '.join(missing_symbols) or 'none'}",
                  f"- Required report types per symbol: {', '.join(REQUIRED_REPORT_SUFFIXES)}",
                  f"- Missing required reports: {', '.join(missing_required_reports) or 'none'}",
                  f"- Invalid required reports: {', '.join(invalid_required_reports) or 'none'}",
                  f"- Funding audits not PASS: {', '.join(nonpassing_funding_audits) or 'none'}",
                  f"- JSON reports inventoried: {len(files)}",
                  "- Missing reports are not inferred to be successful.",
                  "- Reports without an explicit status are labeled REPORT_PRESENT_STATUS_UNSPECIFIED.",
                  "- This inventory does not calculate or certify performance metrics.",
                  ""])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = render(args.root)
    args.output.write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
