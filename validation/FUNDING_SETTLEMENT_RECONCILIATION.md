# Funding Settlement Reconciliation Checklist

Status: RESEARCH ONLY. This checklist is a gate for interpreting historical Funding PnL, not a claim that the backtest has been reconciled.

## Required source data

For each exact futures contract and exchange, retain:
- Raw funding history from the exchange's authoritative endpoint/archive
- Raw mark-price observations at each funding timestamp, if available
- Candle source and timestamp convention (open time, UTC)
- Retrieval timestamp, raw-file hash, symbol/contract identity, and coverage
- Account fee tier and contract multiplier where applicable

## Settlement ledger

For every funding event, produce a row with:
- Symbol/contract and funding timestamp (UTC)
- Funding rate and mark price, with source label
- Position side, size/notional immediately before settlement
- Whether the position was eligible at the settlement instant
- Expected funding cashflow, exchange-reported cashflow (if available), and delta
- Explicit boundary decision for positions opened/closed at or around the timestamp

For a linear USDT-margined contract, a common convention is:
- Long cashflow = - quantity × mark price × funding rate
- Short cashflow = + quantity × mark price × funding rate

Verify this against the specific contract's exchange documentation. Inverse contracts and changing position size require different notional calculations.

## Boundary and price rules

1. Candle OHLC alone cannot determine intrabar ordering between a fill and a funding timestamp. Flag trades whose entry/exit candle contains a settlement event; do not silently treat the event as definitively eligible.
   The current backtest's `funding_boundary_ambiguous` flag is narrower: it marks a held position stopped by an adverse opening gap on a candle whose open timestamp also has a funding event. The aggregate `funding_boundary_ambiguous_trades` counts only those flagged trades; it is not a count of every potentially ambiguous funding boundary. Treat this as a minimum diagnostic, not a complete reconciliation.
2. The existing simulator's interval rule is `entry_candle_open < funding_time <= exit_candle_open`. This is a modeling convention, not proof of exchange eligibility. Compare results under conservative alternate boundary assumptions and isolate affected trades.
3. Prefer actual mark-price observations at settlement. Prior trade-candle close proxies must be tagged, counted, and separately stress-tested; do not call them mark prices.
4. Confirm funding rate timestamps, units, sorting, uniqueness, symbol match, full coverage, and any missing intervals. An 8-hour schedule is only a reference; funding intervals can vary.
5. Reconcile sampled events to exchange records when possible. If no authoritative event/mark data is available, report the limitation and keep the strategy Research-only.

## Acceptance gate

Do not claim Funding PnL is reconciled unless:
- Data provenance and contract identity are established.
- No unexplained duplicate or missing events remain in the evaluated range.
- Mark-price source mix is reported.
- Boundary-ambiguous trades are counted and sensitivity is quantified.
- Sampled event calculations match exchange convention and records within explained rounding/precision.
- Net returns and risk metrics are recomputed with the reconciled ledger.

Passing code tests only confirms tested code behavior; it does not satisfy this reconciliation gate.