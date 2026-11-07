# Score calibration audit protocol (research only)

## Current limitation

The existing EMA-cross research backtest records outcomes but no frozen entry-time model score. The dashboard's confirmed_score is visible at runtime, but signal_history is in-memory and capped, not a durable historical ledger. Neither source currently provides a complete, verified score-to-realized-trade dataset. Do not map EMA parameters, PnL, or arbitrary indicators to scores after the fact.

## Strict linkage helper

research/score_event_ledger.py adds link_realized_trades(score_events, realized_trades). It requires a stable signal_id, frozen entry-time score, symbol, side, signal timestamp, exit timestamp and net return. It fails closed on duplicate IDs, unmatched realized trades, symbol/side mismatches, invalid timestamps and invalid numbers. It explicitly counts score events with no realized trade. Unit coverage is in tests/test_score_event_ledger.py.

**Important:** this helper does not persist events and is not wired into the running dashboard or execution layer. It validates caller-supplied records only. No production score/outcome linkage exists until a durable, restart-safe ledger is implemented and tested. Score calibration has not yet been established.

## Durable local store added

`research/score_event_store.py` adds a SQLite-backed research ledger. Entry events and realized outcomes are inserted transactionally with unique signal IDs; outcomes are rejected if the source event is missing, the symbol/direction differs, the exit precedes the signal, or the ID was already used. `calibration_rows()` reports completed trades and score events without completed outcomes separately, and the test suite checks data survives reopening the database. This is a local library, not yet wired to the live dashboard or runtime signal pipeline; no production records are being collected by this change.

## Cost evidence versus verified net return

The store currently checks whether `fees`, `slippage`, and `funding` are present as finite numeric fields. This is a **coverage check only**: it does not verify the values against exchange statements, validate their units or sign convention, or calculate `net_return` from them. A row with all three fields is labelled `FIELDS_PRESENT_UNVERIFIED`, not verified.

Before a dataset is accepted for model evaluation, the ingestion adapter must document and test:
- Fee units and treatment of maker/taker fees, rebates, and partial fills.
- Slippage convention and whether it is measured from a decision price, quoted price, or simulated fill.
- Funding cashflow sign, funding timestamps, and how funding is allocated across a position's holding interval.
- Return denominator (for example, position notional versus allocated margin), leverage treatment, and whether entry/exit fees are already included.
- Reconciliation from raw fill/cashflow records to the final net return, with stable exchange order/trade IDs and duplicate handling.

Do not subtract cost fields from `net_return` a second time if the supplied return is already net. Conversely, do not accept a return as net solely because these fields exist. Keep unverified or unreconciled records out of primary profitability conclusions and show their counts in coverage diagnostics.

## Required input schema

research.score_calibration.summarize_score_calibration expects one row per completed trade: symbol; score (frozen at entry, 0–100); net_return (after entry/exit fees, slippage and applicable historical funding); and exit_ts (milliseconds). Link using the stable signal/trade ID, never future-derived scores. The current helper accepts caller-supplied `net_return`; it cannot independently prove that the return follows the required convention.

## Evaluation design

1. Freeze score definition and threshold using training/validation data.
2. Keep a chronological, untouched holdout; do not tune on it.
3. Report BTC, ETH, SOL, AVAX and XRP score bands 70–79, 80–89 and 90–100; separate LONG/SHORT where sample size permits.
4. Include all completed trades and disclose filtered/rejected signals.
5. Report count, win rate, mean net expectancy, profit factor, maximum drawdown, costs/funding assumptions and date range.
6. Buckets below 30 trades are INSUFFICIENT_SAMPLE; 30 is not proof of significance.
7. Report the 95% Wilson confidence interval for each bucket's win rate. Treat wide intervals as high uncertainty; do not interpret the point estimate alone as stable performance.
8. Use multiple walk-forward folds and cost stress tests. Do not call scores probabilities without separate independent calibration and validation.

## Safety boundary

These helpers do not retrieve market data, persist records from the running app, authorize trading, or execute orders. No result enables live trading. Merge, deployment and live execution require separate explicit authorization and independent risk gates.
