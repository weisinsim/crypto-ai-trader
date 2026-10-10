# Validation Gate: Findings Before Any Profitability Claim

Reviewed against the current `app.py` implementation on 2026-10-10.

## Verified blockers

1. **The current app is a live dashboard, not a historical backtest.** The repository's current validation script consumes externally supplied frozen signals; it does not recreate the live model's signals over historical candles.
2. **Potential unfinished-candle leakage in signal generation.** `analyze_symbol` assigns `p = c1[-1]` and computes indicators/signals from arrays including the last item, while the dashboard's `last_closed_candle` field is derived from `series[s]["1h"][-2]`. This is a material mismatch unless every caller guarantees that the final array item is closed. The historical signal generator must explicitly drop the in-progress candle and use only data available at the decision time.
3. **Risk sizing is not the screenshot's 0.5% account-risk policy.** The inspected code sets `risk_usd=10.0` as a constant. Position size must instead derive from the configured account equity, risk fraction, entry-stop distance, contract multiplier, and applicable costs/constraints. Until fixed and tested, the dashboard sizing must not be described as 0.5% account risk.
4. **The validation harness does not model funding, partial take profits, overlapping trades, exchange liquidation, or realistic fill/market-impact behavior.** Its output is an exit-distance sensitivity audit only.
5. **The historical baseline numbers in prior notes are not reproducible from the repository alone.** No input data, frozen signal file, or run output establishing those numbers was present in the inspected repository tree.

## Required work before a candidate can be promoted

- Create a deterministic, versioned historical signal generator that uses closed candles only.
- Record each signal's source candle timestamp, model version, asset, direction, entry assumption, ATR, stop, and target in an immutable CSV/JSON artifact.
- Validate candle timestamp continuity, duplicates, missing bars, symbol/timeframe identity, and input-source provenance.
- Apply a strict chronological development / walk-forward / final holdout split; never select parameters on the final holdout.
- Include maker/taker fee scenarios, spread/slippage scenarios, funding where relevant, and conservative intrabar collision rules.
- Define a portfolio-level position policy, max concurrent positions, risk cap, and liquidation safeguards before aggregating per-trade results.
- Report trade count, net expectancy in R, profit factor, drawdown, time-in-market, cost sensitivity, and per-fold dispersion. Include confidence intervals or resampling diagnostics where suitable.
- Keep all models in research status until a sufficient, independent evidence set supports promotion.

## Current status

- Unit-test workflow: passed for commit `007b52ac2ae8abca7bdc3d7a5c76c2aea3d83dd3`.
- Historical profitability validation: **not run**; suitable source data and frozen signals are not yet available in the repository.
- Live trading: **not approved** by this validation gate.

This document is an audit of code and validation requirements, not a claim that a strategy is profitable or unprofitable.
