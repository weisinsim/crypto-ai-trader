"""Out-of-sample score calibration summaries; research-only, no trading authorization.

Input rows must be realized trades from an untouched chronological holdout. Each
row needs symbol, score (0..100), net_return (decimal return after fees/slippage
and any applicable funding), and exit_ts (milliseconds). This module never
infers outcomes from scores or creates trades.
"""
from __future__ import annotations

import math
from collections import defaultdict

DEFAULT_BANDS = ((70.0, 80.0), (80.0, 90.0), (90.0, 100.0000001))


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _max_drawdown(returns):
    equity = peak = 1.0
    worst = 0.0
    for value in returns:
        equity *= 1.0 + value
        peak = max(peak, equity)
        if peak > 0:
            worst = max(worst, (peak - equity) / peak)
    return worst


def summarize_score_calibration(trades, *, min_samples=30, bands=DEFAULT_BANDS):
    """Group realized holdout trades by symbol and score band.

    Returns a JSON-serializable report. A bucket is not marked validated unless
    it meets min_samples; metrics are still emitted so small samples remain visible.
    net_return must already include all modeled trading costs and funding.
    """
    if isinstance(min_samples, bool) or not isinstance(min_samples, int) or min_samples < 1:
        raise ValueError("min_samples must be a positive integer")
    normalized_bands = []
    for lower, upper in bands:
        if not (_number(lower) and _number(upper) and 0 <= lower < upper <= 101):
            raise ValueError("each score band must satisfy 0 <= lower < upper <= 101")
        normalized_bands.append((float(lower), float(upper)))
    groups = defaultdict(list)
    for index, trade in enumerate(trades):
        if not isinstance(trade, dict):
            raise ValueError(f"trade {index} must be a mapping")
        symbol, score, net_return, exit_ts = (
            trade.get("symbol"), trade.get("score"),
            trade.get("net_return"), trade.get("exit_ts"),
        )
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError(f"trade {index} has invalid symbol")
        if not _number(score) or not 0 <= score <= 100:
            raise ValueError(f"trade {index} score must be finite and in [0, 100]")
        if not _number(net_return) or net_return <= -1:
            raise ValueError(f"trade {index} net_return must be finite and greater than -1")
        if not _number(exit_ts) or exit_ts < 0:
            raise ValueError(f"trade {index} exit_ts must be a non-negative finite timestamp")
        match = next((i for i, (lo, hi) in enumerate(normalized_bands)
                      if lo <= score < hi or (score == 100 and hi > 100)), None)
        if match is not None:
            groups[(symbol.strip().upper(), match)].append(
                (float(exit_ts), float(net_return), float(score))
            )

    report = []
    for symbol, band_index in sorted(groups):
        rows = sorted(groups[(symbol, band_index)], key=lambda row: row[0])
        returns = [row[1] for row in rows]
        n = len(rows)
        wins = sum(value > 0 for value in returns)
        gross_profit = sum(value for value in returns if value > 0)
        gross_loss = -sum(value for value in returns if value < 0)
        lower, upper = normalized_bands[band_index]
        report.append({
            "symbol": symbol,
            "score_band": f"{lower:g}-{min(upper, 100):g}" if upper <= 100 else f"{lower:g}-100",
            "sample_count": n,
            "win_rate": wins / n,
            "mean_net_expectancy": sum(returns) / n,
            "profit_factor": gross_profit / gross_loss if gross_loss else (None if gross_profit == 0 else "infinity"),
            "max_drawdown": _max_drawdown(returns),
            "mean_score": sum(row[2] for row in rows) / n,
            "validation_status": "SAMPLE_THRESHOLD_MET" if n >= min_samples else "INSUFFICIENT_SAMPLE",
            "min_samples_required": min_samples,
            "costs_included_by_input_contract": True,
            "out_of_sample_required": True,
            "score_is_probability": False,
        })
    return {
        "status": "RESEARCH_ONLY",
        "trade_count_in_bands": sum(len(rows) for rows in groups.values()),
        "min_samples_required": min_samples,
        "buckets": report,
        "warning": "Use untouched chronological holdout trades only; net_return must include fees, slippage and applicable funding. Score is not a win-probability estimate.",
        "auto_trade_authorized": False,
    }
