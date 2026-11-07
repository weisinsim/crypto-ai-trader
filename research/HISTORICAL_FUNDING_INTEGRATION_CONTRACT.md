# Historical funding event integration contract

Status: research utility implemented; simulator integration intentionally pending.

## Event schema

A funding event is represented as:
- `symbol`: exchange-native perpetual symbol, e.g. `BTCUSDT`
- `funding_timestamp_ms`: UTC Unix timestamp in milliseconds
- `funding_rate`: decimal rate, not percent or basis points

Do not mix spot symbols, coin-margined contracts, or symbols from different venues.
Rates and timestamps must be normalized by a source-specific importer before use.

## Current implementation

`research/funding_history_audit.py` validates symbol consistency, finite rates,
nonnegative integer timestamps, sorted order, and duplicate settlement timestamps.
`calculate_position_funding` applies supplied events on the half-open interval
`[entry_timestamp_ms, exit_timestamp_ms)`. Positive rates are a cost for longs
and a credit for shorts. It assumes constant entry notional.

## Explicit limitations

- `coverage_verified` and `source_verified` are always false.
- No exchange API importer, pagination reconciliation, raw-file hash, or provenance
  manifest is implemented by this utility.
- Constant entry notional does not account for partial TP1 reductions or changing
  mark notional at settlement.
- Candle timestamps in the current paper simulator do not identify the exact
  intrabar exit instant. Feeding those timestamps directly into funding accounting
  could misclassify a settlement near entry/exit.
- The simulator still uses its candle-bucket funding assumption. The new helper is
  not yet wired into net PnL, and should not be described as live historical-funding
  backtesting.
- A configured `funding_bps_per_8h=0` means no funding cost was modeled; it does not
  mean the exchange charged or credited zero. Simulator output explicitly marks
  funding as `ASSUMPTION_ONLY_NOT_HISTORICAL` and warns against that interpretation.

## Gate before integration

1. Add a source-specific importer with pagination, symbol/venue metadata, raw file
   digest, retrieval time, and documented timestamp units.
2. Prove requested interval coverage and expected settlement cadence; fail closed on
   missing pages, duplicate timestamps, gaps, or mixed contracts.
3. Define position lifecycle timestamps and quantity/notional at each settlement.
4. Add tests for event boundaries, partial exits, positive/negative rates, long/short,
   and missing history.
5. Keep event-based and candle-bucket modes separate in reports; never silently
   fall back to the approximation while claiming verified history.
6. Require independent holdout and cost/fill validation before any performance claim.

Research-only. No trading authorization is granted.
