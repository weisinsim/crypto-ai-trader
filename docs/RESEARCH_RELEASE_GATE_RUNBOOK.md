# Research Release Gate Runbook

This runbook separates code-health checks from strategy validation. A green CI pipeline is not evidence of a profitable strategy, and a research coverage inventory is not production approval.

## Current verified repository state

- Main branch contains the funding-audit gate fix requiring an explicit `status: PASS`.
- Post-merge CI for that change passed: Research test suite, Python validation, and Validation audit tests.
- These checks validate code and report-gating behavior. They do not certify live trading readiness.
- A new five-asset historical research run must be checked independently before using its metrics. Do not reuse older dashboard values as if they were current.

## Run a fresh historical study

1. Open [Historical baseline research](../.github/workflows/historical-baseline-research.yml) in GitHub Actions.
2. Select **Run workflow** on `main`.
3. Select a history length (`90`, `180`, or `365` days; use `365` for the full baseline study).
4. Wait for all five asset jobs: BTCUSDT, ETHUSDT, SOLUSDT, AVAXUSDT, and XRPUSDT.
5. Record the workflow run URL, commit SHA, requested date range, and each asset job's final conclusion.

## Per-asset acceptance checklist

An asset is **not comparable** unless all applicable checks below pass:

- [ ] Closed-candle CSV and manifest exist; symbol, interval, bar count, byte size, and SHA-256 agree.
- [ ] Funding CSV exists and the funding audit JSON contains explicit `status: PASS`.
- [ ] Funding coverage and price-source provenance are reviewed; any proxy/unverified price source is disclosed.
- [ ] Baseline and adaptive reports completed using the same declared history window.
- [ ] Walk-forward folds, holdout results, and transaction-cost stress scenarios are present.
- [ ] Results are checked against missing data, timestamp alignment, funding settlement assumptions, and intrabar execution assumptions.
- [ ] Report is linked to the exact workflow run and commit SHA.

A missing audit, absent funding, incomplete stress results, or failed job must be reported as **BLOCKED / NOT COMPARABLE**, not silently treated as a zero-cost backtest.

## Strategy promotion gate

Do not promote a strategy based on win rate alone. Review together:

- Net returns after fees, spread, slippage, and funding.
- Profit factor and expectancy, with trade count and sample-size caveats.
- Maximum drawdown and worst-fold performance.
- Stability across walk-forward folds and the untouched holdout.
- Performance under normal and severe cost assumptions.
- Whether candidate parameters were selected only on training data.

Any failed or missing mandatory gate means **NO PROMOTION**. A research-only candidate gate is not a production approval.

## Dashboard freshness policy

Every displayed research metric should include:

- Run timestamp (UTC), commit SHA, and source workflow URL.
- Asset, timeframe, history start/end, and funding audit status.
- A clear distinction between **latest completed research** and **older historical reference**.
- A stale/unknown label when freshness cannot be verified from a completed run.

Never label the dashboard “live-ready” merely because CI is green or a coverage inventory passes.

## Before live trading

Keep exchange order placement disabled until data provenance, fee/slippage/funding modeling, out-of-sample stability, risk limits, order-state recovery, and paper-trading evidence have been reviewed. This runbook itself is not an approval to enable live orders.
