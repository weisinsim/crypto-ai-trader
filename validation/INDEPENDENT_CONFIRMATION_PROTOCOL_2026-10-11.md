# Next Research Gate: Independent Confirmation Protocol

Date: 2026-10-11  
Repository: `weisinsim/crypto-ai-trader`  
Status: **PROTOCOL ONLY — no new performance claim**

## Current evidence

- Historical baseline research #91 completed across five symbols: https://github.com/weisinsim/crypto-ai-trader/actions/runs/38127055306
- Python validation #198 and audit tests #263 passed on the review commit: https://github.com/weisinsim/crypto-ai-trader/actions/runs/38127711642 and https://github.com/weisinsim/crypto-ai-trader/actions/runs/38127711656
- The adaptive EMA grid selected no candidate for BTC, SOL, XRP, or AVAX. ETH selected a candidate but lost 25.5046% in the approximate 0.30% round-trip stress holdout, with PF 0.5296 and 27.195% maximum drawdown.
- The separate BTC breakout/retest report had 24 short trades and +7.6080% base holdout return, but only +0.0048% at 0.30% round-trip stress and -7.0851% at 0.46%.
- The separate AVAX mean-reversion report had +11.1196% base holdout return, +4.0647% at 0.30% cost, and -2.5496% at 0.46%.
- These strategy reports share historical data and have already been inspected. Their holdout results are consumed and must not be reused to tune parameters.

## Confirmation rules

1. Freeze BTC breakout/retest and AVAX mean-reversion parameters exactly as reported. No further selection based on their inspected holdout outcomes.
2. Source a new, later, untouched chronological window with sufficient 1H bars, or start prospective paper trading. Keep contract, fee tier, slippage assumptions, funding series and candle interval fixed and versioned.
3. Before computing results, reconcile every funding event to authoritative settlement timestamp/rate/mark-price data. Quantify proxy mark-price rows and reject unexplained timestamps.
4. Use conservative execution assumptions: stop is assumed first if stop and target are both hit in one OHLC bar; document settlement/exit ordering; avoid treating hourly candles as proof of intrabar sequence.
5. Predeclare gates before opening confirmation results:
   - at least 30 closed trades for initial statistical screening (prefer 50+ for more informative inference);
   - net return positive after base costs and historical funding;
   - profit factor > 1.15 after base costs;
   - non-negative net return at 0.30% round-trip stress;
   - maximum drawdown no greater than 10% for initial screening;
   - no single fold contributes more than 50% of total positive PnL;
   - results remain directionally stable across at least two chronological subperiods.
6. If any gate fails, retain Research/Reject and do not optimize against the confirmation window. A failed candidate requires a new hypothesis and a future untouched confirmation segment.

## Operational decision

Do not deploy or automate live orders. The immediate next step is to acquire/prepare an untouched confirmation data window or run a prospective paper-trading observation period, not to expand the parameter grid. Green CI is a software correctness signal, not a profitability signal.
