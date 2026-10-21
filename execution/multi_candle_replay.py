"""Multi-symbol candle replay orchestration for research only.

Each scenario supplies its own event and closed OHLC candles. Positions are
simulated independently; this module does not model portfolio margin, shared
capital constraints, exchange data, or order execution.
"""
from __future__ import annotations

from execution.candle_replay import replay_paper_trade
from execution.paper_replay import summarize_closed_trades


def replay_multiple_events(scenarios, *, starting_equity=1000.0):
    """Replay a sequence of independent scenarios and aggregate closed trades.

    A scenario requires event, candles, quantity, and opened_at_ms. Optional
    fee_rate and mark_price are passed through to the single-trade replay.
    Closed-trade metrics use only completed trades; open positions are counted
    separately and are never silently presented as closed performance.
    """
    if not isinstance(scenarios, (list, tuple)):
        raise ValueError("scenarios_must_be_sequence")

    results = []
    seen_ids = set()
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            raise ValueError("invalid_scenario")
        required = ("event", "candles", "quantity", "opened_at_ms")
        if any(key not in scenario for key in required):
            raise ValueError("missing_scenario_field")
        event = scenario["event"]
        if not isinstance(event, dict):
            raise ValueError("invalid_scenario_event")
        event_id = event.get("event_id")
        if event_id is not None:
            if event_id in seen_ids:
                raise ValueError("duplicate_event_id")
            seen_ids.add(event_id)

        kwargs = {
            "quantity": scenario["quantity"],
            "opened_at_ms": scenario["opened_at_ms"],
        }
        if "fee_rate" in scenario:
            kwargs["fee_rate"] = scenario["fee_rate"]
        if "mark_price" in scenario:
            kwargs["mark_price"] = scenario["mark_price"]
        replay = replay_paper_trade(event, scenario["candles"], **kwargs)
        results.append({
            "event_id": event.get("event_id"),
            "symbol": event.get("symbol"),
            "side": event.get("side"),
            **replay,
        })

    closed = [item["trade"] for item in results if item["trade"]["status"] == "CLOSED"]
    closed.sort(key=lambda trade: trade["closed_at_ms"])
    metrics = summarize_closed_trades(closed, starting_equity=starting_equity)
    open_count = sum(item["trade"]["status"] == "OPEN" for item in results)
    return {
        "results": results,
        "closed_trade_metrics": metrics,
        "scenario_count": len(results),
        "closed_trade_count": len(closed),
        "open_trade_count": open_count,
        "research_only": True,
        "orders_sent": 0,
        "portfolio_margin_modeled": False,
    }
