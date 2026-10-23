"""Human-readable formatting for entry-alert notifications."""
from __future__ import annotations

from datetime import datetime, timezone
import math


def _finite_number(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def format_entry_alert(event):
    """Format an alert event; clearly labels research-only candidates."""
    if not isinstance(event, dict) or event.get("eligible") is not True:
        raise ValueError("only eligible alert events can be formatted")

    symbol = event.get("symbol")
    side = event.get("side")
    if not isinstance(symbol, str) or side not in {"LONG", "SHORT"}:
        raise ValueError("alert is missing a valid symbol or direction")

    production_approved = (
        event.get("model_status") == "PRODUCTION"
        and event.get("research_only") is False
        and event.get("production_approval") is True
        and event.get("notification_only") is True
        and event.get("auto_trade_authorized") is False
    )
    status = "PRODUCTION SIGNAL" if production_approved else "RESEARCH CANDIDATE — NOT LIVE-APPROVED"
    score = event.get("score")
    score_text = f"{score:.1f}/100" if _finite_number(score) else "n/a"

    def price(name):
        value = event.get(name)
        return f"{value:.8g}" if _finite_number(value) else "n/a"

    candle = event.get("signal_candle_ms")
    if _finite_number(candle):
        try:
            candle_text = datetime.fromtimestamp(candle / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        except (OverflowError, OSError, ValueError):
            candle_text = "n/a"
    else:
        candle_text = "n/a"

    lines = [
        f"ENTRY SETUP: {symbol} {side}",
        status,
        f"Score: {score_text}",
        f"Entry: {price('entry')}",
        f"Stop loss: {price('stop_loss')}",
        f"Take profit 1: {price('take_profit_1')}",
        f"Take profit 2: {price('take_profit_2')}",
        f"Signal candle: {candle_text}",
        "Execution: notification only; no order was placed.",
    ]
    rr = event.get("risk_reward")
    if _finite_number(rr) and rr > 0:
        lines.insert(4, f"Risk/reward: {rr:.2f}")
    return "\n".join(lines)
