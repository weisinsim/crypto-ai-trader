# Frozen Confirmation Candidates

This directory-level configuration freezes the current BTCUSDT breakout/retest and AVAXUSDT mean-reversion parameter sets for an independent confirmation run. These candidates are **not approved for live trading**; both failed at least one cost-robustness gate on previously inspected holdout data.

- Source configuration: `validation/frozen_confirmation_candidates.json`
- Source workflow: https://github.com/weisinsim/crypto-ai-trader/actions/runs/38127055306
- Freeze commit: `7b0a5575cdf99febf4d9d7a876a2a56292dabced`

## Required behavior of the confirmation runner

1. Read the candidate parameters from the frozen JSON and record its SHA-256 in the report.
2. Refuse to run parameter search or mutate candidate parameters.
3. Require explicit confirmation start/end dates. Reject any overlap with the historical data used by the source workflow.
4. Fetch closed 1H bars and funding events only for the specified interval; validate symbol, unique event times, timestamp grid, and mark-price provenance.
5. Run each candidate separately for 0.14%, 0.30%, and 0.46% round-trip cost assumptions.
6. Report trade count, net return, max drawdown, profit factor, win rate, subperiod stability, PnL concentration, funding PnL, and all source/proxy counts.
7. Use conservative same-bar handling and disclose the remaining hourly-OHLC event/exit ordering ambiguity.
8. Mark the report `RESEARCH_ONLY` regardless of workflow status. No live order placement.

Do not use the previous holdout window for another candidate-selection decision. A green CI run only establishes code/pipeline execution, not profitability.
