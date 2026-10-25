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


def summarize_score_calibration(trades, *, min_samples=30, bands=DEFAULT_BANDS, split_by_side=False):
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
    if not isinstance(split_by_side, bool):
        raise ValueError("split_by_side must be a boolean")
    ordered_bands = sorted(normalized_bands)
    for previous, current in zip(ordered_bands, ordered_bands[1:]):
        if current[0] < previous[1]:
            raise ValueError("score bands must not overlap")
    groups = defaultdict(list)
    input_trade_count = 0
    out_of_band_trade_count = 0
    for index, trade in enumerate(trades):
        if not isinstance(trade, dict):
            raise ValueError(f"trade {index} must be a mapping")
        input_trade_count += 1
        symbol, score, net_return, exit_ts = (
            trade.get("symbol"), trade.get("score"),
            trade.get("net_return"), trade.get("exit_ts"),
        )
        side = trade.get("side")
        if split_by_side and side not in ("LONG", "SHORT"):
            raise ValueError(f"trade {index} side must be LONG or SHORT when split_by_side=True")
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
            key = (symbol.strip().upper(), match, side) if split_by_side else (symbol.strip().upper(), match)
            tie_id = trade.get("signal_id", trade.get("trade_id", ""))
            if tie_id is None:
                tie_id = ""
            if not isinstance(tie_id, str):
                tie_id = str(tie_id)
            # Stable tie-breaking prevents input row order from changing drawdown
            # when multiple trades share the same millisecond exit timestamp.
            groups[key].append((float(exit_ts), tie_id, float(score), float(net_return)))
        else:
            out_of_band_trade_count += 1

    report = []
    for key in sorted(groups):
        symbol, band_index = key[:2]
        side = key[2] if split_by_side else None
        rows = sorted(groups[key], key=lambda row: (row[0], row[1], row[2], row[3]))
        returns = [row[3] for row in rows]
        n = len(rows)
        wins = sum(value > 0 for value in returns)
        gross_profit = sum(value for value in returns if value > 0)
        gross_loss = -sum(value for value in returns if value < 0)
        lower, upper = normalized_bands[band_index]
        bucket = {
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
        }
        if split_by_side:
            bucket["side"] = side
        report.append(bucket)
    return {
        "status": "RESEARCH_ONLY",
        "input_trade_count": input_trade_count,
        "trade_count_in_bands": sum(len(rows) for rows in groups.values()),
        "trade_count_outside_bands": out_of_band_trade_count,
        "min_samples_required": min_samples,
        "split_by_side": split_by_side,
        "buckets": report,
        "warning": "Use untouched chronological holdout trades only; net_return must include fees, slippage and applicable funding. Score is not a win-probability estimate.",
        "auto_trade_authorized": False,
    }
