# Live-vs-replay parity gate report

Last reviewed: 2026-10-18

## Resolved implementation gaps

The following code-level gaps have regression coverage and have passed the required CI checks on merged commits:

- Live and replay VWAP use quote-volume inputs.
- Replay can optionally export hourly state rows with `--include-states`, including directional `WATCH-LONG` and `WATCH-SHORT` labels.
- XRP replay preserves the model's WATCH-LONG score of 43 when the kill switch is clear but a full entry condition is not met.
- Replay stop and target levels use the same zero-ATR fallback as the live dashboard: 0.5% of price for BTC/SOL/AVAX and 1% for XRP.
- Replay input rejects sub-millisecond timestamps, duplicate/out-of-order candles, malformed OHLCV, and missing 1h/4h intervals.

## Not yet proven

- Full signal-by-signal parity has not been measured across every candle for BTCUSDT, ETHUSDT, SOLUSDT, AVAXUSDT and XRPUSDT against frozen live-equivalent fixtures.
- Runtime Binance and Bybit timestamp semantics need independent verification against the actual adapter responses; replay files use candle close timestamps, while live adapter rows use candle open timestamps before closed-candle filtering.
- The replay/exit audit does not fully model partial take profits, trailing stops, order-book execution, market impact, liquidation, portfolio-level overlapping exposure, or exact funding cash flows and event timing.
- Historical code replay does not establish that a signal was actually emitted live.
- Passing CI verifies code/test contracts, not strategy profitability.

## Required promotion gates

1. Verify timestamp semantics for each runtime feed and preserve only fully closed bars.
2. Run complete state replay from verified, gap-audited historical data for all five symbols.
3. Compare signal, score, entry, stop, TP1, TP2, WATCH and NO-TRADE outputs against frozen live-equivalent fixtures; publish mismatch counts and examples.
4. Run walk-forward tests with parameter selection confined to training folds, realistic fees/funding, an untouched holdout, and stress costs.
5. Report every failed fold, missing data segment, drawdown, profit factor, and trade count. Do not tune on the untouched holdout.
6. Only after every mandatory research and execution-risk gate passes should a frozen candidate enter paper trading and a separate production review.

## Decision

**NO PROMOTION. Keep live order placement disabled.** Prior research findings showed material cost-robustness failures across the tested asset set. These implementation fixes improve data integrity and replay parity; they do not change that research decision.

A green CI run is not a strategy approval. Replay results are exploratory until the gates above are completed.
