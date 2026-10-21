"""Deterministic replay reporting helpers for paper-trade ledgers.

This module reports simulated results only. It does not fetch market data, create
signals, connect to an exchange, or place orders. Callers must provide completed
paper-trade dictionaries in chronological close order.
"""
from __future__ import annotations

import math


def summarize_closed_trades(trades, *, starting_equity=1000.0):
    """Return net-performance metrics for closed paper trades.

    Drawdown is computed on starting equity plus cumulative realized net PnL
    after each trade. Open trades are rejected to avoid silently omitting risk.
    """
    if (not isinstance(starting_equity, (int, float))
            or isinstance(starting_equity, bool)
            or not math.isfinite(float(starting_equity))
            or starting_equity <= 0):
        raise ValueError("invalid_starting_equity")
    if not isinstance(trades, (list, tuple)):
        raise ValueError("trades_must_be_sequence")

    pnls = []
    total_fees = 0.0
    total_funding = 0.0
    for trade in trades:
        if not isinstance(trade, dict) or trade.get("status") != "CLOSED":
            raise ValueError("all_trades_must_be_closed")
        required = ("realized_gross_pnl", "fees_paid", "funding_paid")
        values = [trade.get(key) for key in required]
        if any(not isinstance(v, (int, float)) or isinstance(v, bool)
               or not math.isfinite(float(v)) for v in values):
            raise ValueError("invalid_trade_cost_or_pnl")
        gross, fees, funding = map(float, values)
        net = gross - fees - funding
        pnls.append(net)
        total_fees += fees
        total_funding += funding

    equity = float(starting_equity)
    peak = equity
    max_drawdown = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak)

    wins = sum(pnl > 0 for pnl in pnls)
    losses = sum(pnl < 0 for pnl in pnls)
    gross_profit = sum(pnl for pnl in pnls if pnl > 0)
    gross_loss = -sum(pnl for pnl in pnls if pnl < 0)
    count = len(pnls)
    return {
        "trade_count": count,
        "win_count": wins,
        "loss_count": losses,
        "win_rate": wins / count if count else 0.0,
        "net_pnl": sum(pnls),
        "gross_profit_net_of_costs": gross_profit,
        "gross_loss_net_of_costs": gross_loss,
        "profit_factor": gross_profit / gross_loss if gross_loss else (math.inf if gross_profit else 0.0),
        "fees_paid": total_fees,
        "funding_paid": total_funding,
        "starting_equity": float(starting_equity),
        "ending_equity": equity,
        "max_drawdown_pct": max_drawdown * 100.0,
        "open_trades_included": 0,
        "research_only": True,
    }
