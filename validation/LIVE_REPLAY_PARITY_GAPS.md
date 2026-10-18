# Live-vs-replay parity gap report

## Current status (2026-10-18)

Resolved in the latest merged changes:
- Replay now uses quote volume for VWAP, matching the live analysis input.
- Replay can optionally export hourly WATCH and NO-TRADE states with directional `WATCH-LONG` / `WATCH-SHORT` labels using `--include-states`.
- XRP replay now preserves the live model's WATCH-LONG score of 43 when its kill switch is clear but no full entry condition is met.
- Directional state classification is covered by direct unit tests.

Still not proven:
- Full signal-by-signal parity across every candle and all five symbols has not yet been measured against frozen live fixtures.
- Feed timestamp contracts must still be verified for every runtime adapter.
- The current replay and exit audit do not fully model funding, partial take profits, trailing stops, order-book execution, market impact, liquidation, or overlapping portfolio exposure.
- Passing unit tests verifies implementation contracts, not profitability.

## Required next gates

1. Verify the open-time timestamp contract for Binance and Bybit runtime feeds.
2. Generate a complete state replay from verified, gap-audited historical data for BTCUSDT, ETHUSDT, SOLUSDT, AVAXUSDT and XRPUSDT.
3. Compare signal, score, entry, stop, TP1, TP2, WATCH and NO-TRADE outputs against frozen live-equivalent fixtures.
4. Run cost/funding-aware walk-forward and untouched holdout tests; report every failed fold and missing data segment.
5. Keep live order placement disabled until the research promotion gate is explicitly passed.

Historical code replay is a research reconstruction, not evidence that a signal was actually emitted live and not a profitability guarantee.

---

## Status after closed-candle patch

The live `app.py` now calls `closed_series_rows(s, interval)` inside `analyze_symbol()` for both 1h and 4h. That helper calls `filter_closed_candles`, which admits a candle only when its open timestamp plus the interval duration is no later than the current UTC epoch time. This removes the latest still-forming candle from the live indicator input under the expected exchange timestamp contract.

Regression coverage was added in:
- `tests/test_candle_closure.py` (duration and boundary behavior)
- `tests/test_app_closed_candle_guard.py` (static guard that live analysis uses both filtered intervals)
- `tests/test_indicator_parity.py` (numeric parity checks for EMA, RSI, ATR, ADX, VWAP, support/resistance)

These tests have been committed but their latest CI result must still be checked before calling the patch verified.

## Remaining parity work

1. Confirm the runtime feed timestamps always represent candle open time for both Binance and the Bybit fallback.
2. Run the complete CI suite and repair any failures.
3. Compare replay and live decision outputs on frozen fixtures for every asset family, including entry, stop, target, score and WATCH/NO-TRADE transitions.
4. Reconcile replay behavior with the live model library and any symbol-specific overrides before performance evaluation.

## Additional observations

- Replay indicator functions are intended to mirror `app.py`; the numeric tests are now present, but they are not yet confirmed passing.
- Replay currently records only LONG/SHORT entry candidates, not the full UI state machine.
- Historical signal replay is a research reconstruction; it does not prove those signals were emitted in live operation.
- Funding, partial take profits, trailing stops, order-book execution, market impact, liquidation and overlapping portfolio exposure are not fully modeled by the current exit audit.

## Gate

Until CI passes and the signal-by-signal parity work is complete, treat any resulting backtest as exploratory. Do not promote a model to production based on replay output alone.
