# Live Trading Release Gate

Status: **BLOCKED — no production order submission is authorized by this checklist.**

This checklist is an acceptance record, not proof that any item has passed. Update an item only with linked evidence from the exact release commit and deployment configuration. A passing unit-test suite alone is insufficient.

## Required gates

- [ ] **Frozen strategy definition:** versioned parameters, universe, timeframes, signal rules, entry/exit rules, and model version are fixed before evaluating the final holdout.
- [ ] **Leakage-free validation:** chronological train/validation/holdout or walk-forward reports; closed-candle enforcement; no tuning on the final holdout.
- [ ] **Representative market data:** per-symbol candle coverage and gaps audited; symbol/contract identity verified; raw data hashes and retrieval ranges recorded.
- [ ] **Execution-cost model:** exchange fee tier, spread, slippage, funding events and mark-price provenance documented; base, stressed and severe-cost results produced.
- [ ] **Funding reconciliation:** ambiguous settlement boundaries and missing/duplicate events reviewed; funding PnL reconciled to authoritative records or explicitly excluded from any profitability claim.
- [ ] **Out-of-sample risk limits:** return, max drawdown, profit factor, trade count, tail loss and per-market-regime results reviewed against limits set before the test.
- [ ] **Signal/replay parity:** live closed-candle signals match the frozen offline implementation for recorded fixtures and a representative replay window.
- [ ] **Testnet lifecycle soak:** sustained testnet run covers entries, partial fills, stop/target fills, sibling-order handling, reconnects, rate limits, restarts and ambiguous network outcomes.
- [ ] **Durable reconciliation:** restart recovery reconciles exchange positions, open orders, fills and local ledger before any new entry; ambiguous attempts and stale locks remain fail-closed.
- [ ] **Independent risk controls:** persistent kill switch, daily-loss cap, exposure/notional cap, max-position cap, stale-data gate and execution-error circuit breaker are tested through restart and fault injection.
- [ ] **Operational deployment:** process supervision, health/heartbeat monitoring, log retention, clock synchronization, database backup/restore and incident runbook are verified.
- [ ] **Credential security:** production keys are not stored in source control or logs, use least privilege, and have withdrawals disabled; IP restrictions and rotation are verified where supported.
- [ ] **Operator sign-off:** explicit approval records the exact commit, config hash, account/contract mode, initial capital, leverage cap, risk per trade, daily stop and rollback procedure.
- [ ] **Restricted canary:** only after all preceding gates pass, separately approve a minimal-notional canary with close monitoring and a documented immediate disable path.

## Release decision

Until every required gate has evidence and the operator explicitly approves the exact release, keep the system in research/testnet/notification-only mode. Do not infer production readiness from a passing CI run, a successful testnet request, a paper-trading result, or a single backtest.

A canary approval does not imply permission to scale. Review execution quality and reconciliation after the canary before any increase in exposure.
