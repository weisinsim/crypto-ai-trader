#!/usr/bin/env python3
"""Combine per-symbol exit-audit evidence into a portfolio stop-risk audit.

Each --audit argument is SYMBOL=PATH, where PATH is an exit_parameter_audit JSON
report. The risk_pct_per_trade is an explicit planned stop-risk assumption, not
inferred from PnL. This adapter only aggregates the selected reported trade set;
it does not simulate fills, correlation, or dynamic position sizing.
"""
import argparse
import json
import sys
from pathlib import Path

# Support both `python -m validation.portfolio_risk_from_exit_audits` and
# direct script execution from outside the repository root.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from validation.portfolio_risk_audit import audit_portfolio_risk


def combine_audit_trades(audit_specs, period, target, trade_set, risk_pct_per_trade):
    if not audit_specs:
        raise ValueError("At least one symbol audit is required")
    if not isinstance(risk_pct_per_trade, (int, float)) or isinstance(risk_pct_per_trade, bool):
        raise ValueError("risk_pct_per_trade must be a positive number")
    import math
    if not math.isfinite(risk_pct_per_trade) or risk_pct_per_trade <= 0:
        raise ValueError("risk_pct_per_trade must be finite and positive")
    if period not in ("development", "holdout"):
        raise ValueError("period must be development or holdout")
    if trade_set not in ("signal_level", "single_position"):
        raise ValueError("trade_set must be signal_level or single_position")

    combined = []
    for symbol, path in audit_specs:
        symbol = str(symbol).strip().upper()
        if not symbol:
            raise ValueError("Symbol in --audit must not be empty")
        report = json.loads(Path(path).read_text(encoding="utf-8"))
        try:
            section = report["periods"][period][target]
            trades = section["signal_level_trades" if trade_set == "signal_level" else "single_position_trades"]
        except (KeyError, TypeError) as exc:
            raise ValueError(f"Audit {path!r} is missing period/target/trade-set data") from exc
        if not isinstance(trades, list):
            raise ValueError(f"Audit {path!r} trade evidence must be a list")
        for index, trade in enumerate(trades):
            if "timestamp" not in trade or "exit_timestamp" not in trade:
                raise ValueError(f"Audit {path!r} trade {index} lacks entry/exit timestamps")
            combined.append({
                "trade_id": f"{symbol}:{period}:{target}:{trade_set}:{index}",
                "symbol": symbol,
                "timestamp": trade["timestamp"],
                "exit_timestamp": trade["exit_timestamp"],
                "risk_pct": float(risk_pct_per_trade),
            })
    return combined


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", action="append", required=True, metavar="SYMBOL=PATH",
                        help="Per-symbol exit-audit JSON; repeat once per symbol")
    parser.add_argument("--period", choices=("development", "holdout"), default="holdout")
    parser.add_argument("--target", choices=("target_2.5R", "target_3.0R"), default="target_2.5R")
    parser.add_argument("--trade-set", choices=("signal_level", "single_position"), default="signal_level")
    parser.add_argument("--risk-pct-per-trade", type=float, required=True,
                        help="Explicit planned stop-risk as percent of equity per trade, e.g. 0.5")
    parser.add_argument("--max-portfolio-risk-pct", type=float, default=2.0)
    parser.add_argument("--max-symbol-risk-pct", type=float, default=1.0)
    parser.add_argument("--out", default="portfolio_risk_audit.json")
    args = parser.parse_args()

    specs = []
    for value in args.audit:
        if "=" not in value:
            parser.error("--audit must use SYMBOL=PATH")
        symbol, path = value.split("=", 1)
        if not symbol.strip() or not path.strip():
            parser.error("--audit must use non-empty SYMBOL=PATH")
        specs.append((symbol, path))
    trades = combine_audit_trades(specs, args.period, args.target, args.trade_set,
                                  args.risk_pct_per_trade)
    result = audit_portfolio_risk(trades, args.max_portfolio_risk_pct, args.max_symbol_risk_pct)
    result["source"] = {
        "period": args.period, "target": args.target, "trade_set": args.trade_set,
        "risk_pct_per_trade": args.risk_pct_per_trade,
        "symbols": sorted({trade["symbol"] for trade in trades}),
        "notice": "Risk is a fixed per-trade assumption supplied by the operator; it is not inferred from the exit audit."
    }
    Path(args.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
