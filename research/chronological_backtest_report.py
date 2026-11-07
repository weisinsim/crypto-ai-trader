"""Chronological research report with isolated development and holdout cohorts.

This orchestration partitions by signal time before computing cohort summaries.
It is simulation-only and cannot prove the holdout was unseen or untuned.
"""
from __future__ import annotations

from research.chronological_holdout import partition_chronological_holdout
from research.paper_backtest_report import build_paper_backtest_report


def build_chronological_backtest_report(trades, *, cutoff_ts):
    """Partition completed/censored simulation rows and report each cohort.

    Each row must include trade_id, signal_ts, exit_ts plus the paper report
    schema. The report uses exit_ts as exit_timestamp_ms for performance ordering.
    Development and holdout rows are passed separately to avoid metric mixing.
    """
    partition = partition_chronological_holdout(trades, cutoff_ts=cutoff_ts)

    def report(rows):
        normalized = []
        for row in rows:
            item = dict(row)
            item["exit_timestamp_ms"] = item["exit_ts"]
            normalized.append(item)
        return build_paper_backtest_report(normalized)

    development_report = report(partition["development"])
    holdout_report = report(partition["holdout"])
    return {
        "status": "RESEARCH_ONLY_CHRONOLOGICAL_SIMULATION",
        "cutoff_ts": partition["cutoff_ts"],
        "boundary_rule": partition["boundary_rule"],
        "development": development_report,
        "holdout": holdout_report,
        "development_trade_count": partition["development_count"],
        "holdout_trade_count": partition["holdout_count"],
        "cohorts_disjoint": partition["cohorts_disjoint"],
        "chronological_order_verified": partition["chronological_order_verified"],
        "holdout_unseen_or_untuned_verified": False,
        "simulation_only": True,
        "verified_score_calibration": None,
        "warning": "Development and holdout summaries are separated by signal timestamp. This does not prove holdout secrecy, upstream feature integrity, or verified costs/fills; simulated outcomes are not verified exchange performance.",
        "auto_trade_authorized": False,
    }
