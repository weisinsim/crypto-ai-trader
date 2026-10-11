# Post-Holdout Confirmation Plan

Status: RESEARCH ONLY. This document does not authorize live trading.

## Why a new confirmation window is required

The current ETH mean-reversion and BTC breakout/retest holdout results have already been inspected. Repeated parameter selection using those results would turn them into development data. Do not describe them as untouched after this point.

## Frozen research leads (not approved strategies)

1. ETHUSDT mean reversion: window=72, entry_z=2.0, stop_atr=1.5, direction=SHORT, rsi_filter=0. The inspected historical holdout was positive at base and 0.30% round-trip stress but negative at 0.46% stress.
2. BTCUSDT breakout/retest: BOTH-direction diagnostic showed positive base and slightly positive 0.30% stress results, but negative 0.46% stress. Long and short contributions differed materially.

These are candidates to test, not recommendations to trade. No further parameter changes may be justified using the previously inspected holdout.

## Confirmation procedure

1. Freeze the code commit, model parameters, indicator definitions, decision timing, order types, fees, slippage assumptions, funding logic, and position sizing before collecting confirmation results.
2. Start a prospective paper-trading stream on closed 1H candles. Store every decision, including no-trade decisions, with UTC timestamp, candle timestamp, feature snapshot, model version, entry/exit, stop/targets, position size, funding settlements, fees, slippage estimate, and rejection reason.
3. Never use unfinished candles. Entries may only execute after the signal candle closes; use a documented next-bar fill convention and conservative intrabar stop/target collision rules.
4. Record actual exchange funding timestamps and rates for the exact contract. Reconcile funding events against position exposure at each settlement; record mark-price provenance.
5. Keep each candidate separate. Report long and short legs independently, and do not combine results to conceal a losing side.
6. Evaluate at least the base, 0.30%, and 0.46% round-trip cost assumptions on the exact same frozen signals. State whether the base assumptions reflect the intended venue and account fee tier.
7. Use predeclared gates: adequate trade count, positive net expectancy and PF > 1 across the required cost scenarios, acceptable maximum drawdown, no material look-ahead/data-integrity failures, and stability across chronological subperiods. The numeric minimum trade count and drawdown limit must be set before viewing confirmation outcomes.
8. If a gate fails, mark the candidate Research/Reject. Any revised parameters become a new version and require a new prospective confirmation window.

## Required report

For each asset and strategy, publish code commit, configuration hash, data range, number of signals and trades, exposure, net PnL, fees, slippage, funding cash flow, win rate, expectancy in R, profit factor, maximum drawdown, long/short breakdown, cost sensitivity, and all skipped/invalid data intervals.

## Promotion policy

- Research: incomplete evidence or unpassed gates.
- Candidate: historical walk-forward and holdout gates pass, awaiting prospective confirmation.
- Production-eligible: only after prospective paper trading, operational risk controls, exchange-specific execution and funding reconciliation, and all predeclared gates pass.

A successful CI run proves only that the tested code checks passed; it does not prove profitability.