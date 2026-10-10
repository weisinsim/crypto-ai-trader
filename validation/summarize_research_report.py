#!/usr/bin/env python3
"""Render a concise, non-promotional Markdown report from the batch exit-audit manifest."""
import argparse
import json
import math
from pathlib import Path


def fmt(value, digits=2):
    if value is None:
        return "—"
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and math.isfinite(value):
        return f"{value:.{digits}f}"
    return "—"


def target_metrics(report, target, mode, period="holdout"):
    key = "development_metrics_by_exit" if period == "development" else "holdout_metrics_by_exit"
    metrics_by_exit = report.get(key, {})
    target_data = metrics_by_exit.get(f"target_{target}R", {})
    metrics = target_data.get(mode, {})
    return metrics if isinstance(metrics, dict) else {}


def render(manifest):
    reports = manifest.get("reports", [])
    lines = [
        "# Historical multi-asset research summary",
        "",
        "> Research-only report. A green workflow means the pipeline completed, not that a strategy is profitable. Replay parity against live production logic is not yet established.",
        "",
        f"- Minimum Holdout trades required per exit/mode: **{manifest.get('min_trades_threshold', 'unknown')}**",
        f"- Historical funding required: **{manifest.get('historical_funding_required', 'unknown')}**",
        f"- Cost assumptions: **{', '.join(str(x) for x in manifest.get('cost_sensitivity_bps', []))} bps round trip**",
        "",
        "## Coverage and sample status",
        "",
        "| Symbol | Status by cost | Holdout trade range |",
        "|---|---|---:|",
    ]
    for symbol in sorted({r.get("symbol", "unknown") for r in reports}):
        rows = [r for r in reports if r.get("symbol") == symbol]
        statuses = ", ".join(f"{r.get('cost_bps', '—')} bps: {r.get('status', 'UNKNOWN')}" for r in rows)
        counts = [r.get("holdout_min_trade_count_across_exit_variants") for r in rows
                  if isinstance(r.get("holdout_min_trade_count_across_exit_variants"), int)]
        max_counts = [r.get("holdout_max_trade_count_across_exit_variants") for r in rows
                      if isinstance(r.get("holdout_max_trade_count_across_exit_variants"), int)]
        count_text = f"{min(counts)}–{max(max_counts)}" if counts and max_counts else "—"
        lines.append(f"| {symbol} | {statuses} | {count_text} |")
    lines.extend([
        "",
        "## Development vs Holdout comparison",
        "",
        "Compare the same target, cost, and execution mode across the chronological development and Holdout periods. A strong Holdout result does not erase weak development performance.",
        "",
        "| Symbol | Cost (bps) | Target | Mode | Dev trades | Dev PF | Dev Net R | Holdout trades | Holdout PF | Holdout Net R |",
        "|---|---:|---:|---|---:|---:|---:|---:|---:|---:|",
    ])
    for report in sorted(reports, key=lambda r: (r.get("symbol", ""), r.get("cost_bps", -1))):
        if report.get("status") not in ("RESEARCH_ONLY", "INSUFFICIENT_SAMPLE"):
            continue
        for target in ("2.5", "3.0"):
            for mode in ("signal_level", "single_position"):
                dev = target_metrics(report, target, mode, "development")
                hold = target_metrics(report, target, mode, "holdout")
                if not dev and not hold:
                    continue
                lines.append(
                    f"| {report.get('symbol', '—')} | {report.get('cost_bps', '—')} | {target}R | {mode} | "
                    f"{dev.get('trades', '—')} | {fmt(dev.get('profit_factor'), 4)} | {fmt(dev.get('net_R'), 4)} | "
                    f"{hold.get('trades', '—')} | {fmt(hold.get('profit_factor'), 4)} | {fmt(hold.get('net_R'), 4)} |"
                )
    lines.extend([
        "",
        "## Development/Holdout stability flags",
        "",
        "Flags below are diagnostics, not model rankings. A positive Holdout result paired with negative development performance is treated as regime instability, not proof of edge.",
        "",
        "| Symbol | Cost (bps) | Target | Mode | Dev Net R | Holdout Net R | Diagnostic |",
        "|---|---:|---:|---|---:|---:|---|",
    ])
    for report in sorted(reports, key=lambda r: (r.get("symbol", ""), r.get("cost_bps", -1))):
        if report.get("status") not in ("RESEARCH_ONLY", "INSUFFICIENT_SAMPLE"):
            continue
        for target in ("2.5", "3.0"):
            for mode in ("signal_level", "single_position"):
                dev = target_metrics(report, target, mode, "development")
                hold = target_metrics(report, target, mode)
                if not dev or not hold:
                    continue
                dev_net, hold_net = dev.get("net_R"), hold.get("net_R")
                if not isinstance(dev_net, (int, float)) or not isinstance(hold_net, (int, float)):
                    diagnostic = "METRICS_MISSING"
                elif dev_net < 0 <= hold_net:
                    diagnostic = "REGIME_INSTABILITY: dev loss / holdout gain"
                elif dev_net >= 0 > hold_net:
                    diagnostic = "GENERALIZATION_FAILURE: dev gain / holdout loss"
                elif dev_net < 0 and hold_net < 0:
                    diagnostic = "NEGATIVE_BOTH_PERIODS"
                else:
                    diagnostic = "POSITIVE_BOTH_PERIODS; still check sample/cost gates"
                lines.append(
                    f"| {report.get('symbol', '—')} | {report.get('cost_bps', '—')} | {target}R | {mode} | "
                    f"{fmt(dev_net, 4)} | {fmt(hold_net, 4)} | {diagnostic} |"
                )
    lines.extend([
        "",
        "## Holdout metrics by cost, target, and execution mode",
        "",
        "| Symbol | Cost (bps) | Target | Mode | Trades | Win rate % | PF | Net R | Avg R | Max DD R | Max win R | Largest win % | Top 3 wins % |",
        "|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for report in sorted(reports, key=lambda r: (r.get("symbol", ""), r.get("cost_bps", -1))):
        if report.get("status") not in ("RESEARCH_ONLY", "INSUFFICIENT_SAMPLE"):
            continue
        for target in ("2.5", "3.0"):
            for mode in ("signal_level", "single_position"):
                m = target_metrics(report, target, mode)
                if not m:
                    continue
                lines.append(
                    f"| {report.get('symbol', '—')} | {report.get('cost_bps', '—')} | {target}R | {mode} | "
                    f"{m.get('trades', '—')} | {fmt(m.get('win_rate_pct'))} | {fmt(m.get('profit_factor'), 4)} | "
                    f"{fmt(m.get('net_R'), 4)} | {fmt(m.get('avg_R'), 4)} | {fmt(m.get('max_drawdown_R'), 4)} | "
                    f"{fmt(m.get('max_win_R'), 4)} | {fmt(m.get('largest_win_share_pct'))} | "
                    f"{fmt(m.get('top_three_wins_share_pct'))} |"
                )
    lines.extend([
        "",
        "## Interpretation gates",
        "",
        "- INSUFFICIENT_SAMPLE, NO_SIGNALS, INSUFFICIENT_DATA, FUNDING_INTEGRITY_FAILED, and DATA_INTEGRITY_FAILED are not validated candidates.",
        "- Compare results across all costs and both execution modes; do not select a model from win rate or one favorable cost setting alone.",
        "- Net R is the sum of per-trade R outcomes, not portfolio return or account-level ROI. Max DD R is trade-sequence drawdown in R, not account drawdown.",
        "- This exit audit compares fixed 2.5R and 3R targets on replayed signals. It is not a full portfolio simulator and does not prove indicator parity, realistic fills, liquidation behavior, or live profitability.",
        "",
    ])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    report = render(manifest)
    destination = Path(args.out)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
