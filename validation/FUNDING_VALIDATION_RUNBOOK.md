# Funding-Aware Historical Validation Runbook

## Purpose

This runbook defines the minimum reproducible evidence needed before any strategy is promoted. Passing unit tests is not evidence of profitability.

## Current execution blocker

As checked on 2026-10-11, the following GitHub Actions runs were still queued:
- Validation audit tests: https://github.com/weisinsim/crypto-ai-trader/actions/runs/38109489481
- Python validation: https://github.com/weisinsim/crypto-ai-trader/actions/runs/38109489458
- Historical baseline research: https://github.com/weisinsim/crypto-ai-trader/actions/runs/38109484333

Check these runs again before interpreting any results.

## Data freeze

For each asset (BTC, ETH, SOL, AVAX, XRP), freeze and record:
- Exchange, contract type, symbol, timeframe, UTC coverage window
- Candle and funding source URLs/endpoints, retrieval time, row counts, file hashes
- Candle timestamps sorted and unique; missing intervals and duplicates reported
- Funding timestamps sorted and unique; cadence gaps reported against symbol-specific history
- Mark-price provenance: exchange mark price versus proxy; proxy rows must be flagged
- Explicit confirmation that every signal uses only information available at its decision timestamp

Do not infer complete funding coverage from an 8-hour reference schedule alone.

## Deterministic signal replay

Each signal artifact must include:
- asset, strategy/model version, direction
- decision timestamp and source candle timestamp
- entry assumption, initial stop, target(s), ATR and relevant features
- closed-candle-only indicator values
- signal ID and reproducible data/version identifiers

Reject artifacts with duplicate IDs, future timestamps, unfinished candles, symbol/timeframe mismatch, or untraceable inputs.

## Funding accounting

For each position, settle funding only when the position is eligible at the exchange's funding timestamp. Apply the exchange's signed rate and the correct position notional/mark price convention. Record each event in the trade ledger.

A simplified linear-contract convention, subject to exchange/product rules, is:
- long funding cash flow = - position notional at settlement × funding rate
- short funding cash flow = + position notional at settlement × funding rate

Entry exactly at a funding timestamp must follow the exchange's documented eligibility rule; do not count funding solely because a trade timestamp rounds to that instant. Exit-at-settlement boundary behavior must be explicitly tested. Account for position size changes from partial exits.

## Costs and execution

Run the same frozen signals under:
- baseline configured fee/slippage assumptions
- 0.30% total pressure-cost scenario
- 0.46% total pressure-cost scenario

Document whether each percentage is per side or round trip; never mix these conventions. Model spread, slippage, fees, funding, partial take-profits, overlapping positions, conservative intrabar stop/target collisions, and liquidation constraints where applicable.

## Validation split

Use chronological development and walk-forward folds, followed by a final untouched holdout. Never tune parameters against the final holdout. Freeze model parameters before scoring each evaluation window.

## Required per-asset and per-fold outputs

- number of trades and exposure/time-in-market
- gross PnL, fees, slippage, funding cash flow, net PnL
- net expectancy in R and profit factor
- win rate, average win/loss, maximum drawdown
- results by long/short, market regime, and fold
- cost sensitivity and confidence/resampling diagnostics where appropriate
- data-quality gate, code commit, parameter hash, input-data hashes

## Promotion policy

- Research: incomplete data, insufficient trades, unstable parameters, or any failed integrity gate
- Candidate: all data/replay gates pass and independent performance is promising, but forward validation remains
- Production-eligible: only after independent holdout, realistic cost stress, portfolio risk, and paper-trading evidence pass predefined thresholds
- Reject: negative or unstable net expectancy, failed data integrity, or unacceptable drawdown

No model is approved for live trading by a unit-test pass alone. Thresholds must be declared before the final holdout is scored.
