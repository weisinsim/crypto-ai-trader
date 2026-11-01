# V12 + CVD Independent Validation Plan

**Status: RESEARCH PLAN ONLY — no model is promoted, no live execution is authorized.**

## Purpose

Establish a reproducible, asset-specific validation matrix for BTCUSDT, ETHUSDT, SOLUSDT, AVAXUSDT and XRPUSDT. Evaluate LONG and SHORT as separate hypotheses and measure whether the causal Binance kline taker-buy-volume CVD proxy adds out-of-sample value.

This plan does not change the live dashboard, model defaults, alert contract, order routing, deployment, or any existing pull request.

## Baseline and data boundaries

- Baseline engine: `V12.0-Adaptive-Institutional-Hybrid`.
- CVD input: 1h Binance USD-M kline taker-buy base volume; proxy delta is `2 * taker_buy_base_volume - total_base_volume`, normalized by total base volume over 12h and 24h windows. This is an aggressor-volume proxy, not tick-level exchange CVD.
- Existing historical study ending 2026-10-01 UTC has been reviewed during iterative development. Treat it as **consumed development data**, not a pristine final holdout.
- New forward confirmation begins 2026-10-01 UTC. As of 2026-11-01, this is only about one month of observations; do not label it production-grade until it contains sufficient trades and market regimes.
- Never use a forward confirmation window to tune thresholds, weights, exits, direction, CVD mode, or asset profile. If it is inspected to make a model change, retire it as a holdout and reserve a later window.
- Keep per-symbol candle and funding datasets separate. Preserve exact source files, UTC boundaries, row counts, pagination/coverage diagnostics, SHA-256 hashes, code commit, and parameter hashes.

## Experiment matrix

For each of the five symbols, run these four distinct research arms:

1. LONG, CVD ON
2. LONG, CVD OFF
3. SHORT, CVD ON
4. SHORT, CVD OFF

This produces 20 symbol/direction/feature arms. CVD OFF must neutralize only the CVD feature (direction = UNKNOWN; ratio fields unavailable) while leaving all other inputs byte-for-byte identical. CVD ON must use only closed-candle data available at the decision timestamp. Do not substitute fabricated CVD when taker-buy fields are missing; mark the row unavailable and fail closed or exclude the affected interval with an explicit coverage report.

LONG and SHORT must be evaluated independently. Do not infer side-specific profitability by filtering a combined equity curve after the fact. The simulator must constrain signal generation to the selected side before trade entry and reversal handling.

## Chronological evaluation protocol

### Development and selection

- Use the consumed pre-2026-10-01 history only for code debugging, feature parity, training, and candidate screening.
- All threshold, score, weight, exit, and per-coin parameter selection occurs strictly inside chronological training data.
- Use expanding/rolling walk-forward folds. Report each fold separately; no averaging away losing windows.
- Select at most one frozen candidate per symbol and direction. CVD ON/OFF choice is made using training and walk-forward evidence only.
- Apply a predeclared candidate-count/multiple-testing correction or keep the search grid small and report all tried candidates. Never select the best of 20 arms using the final holdout.

### Forward confirmation

- Freeze code SHA, model version, direction, CVD mode, parameters, costs, and risk assumptions before evaluating forward confirmation.
- Evaluate the frozen candidate once on the untouched forward window.
- If the sample is insufficient, status remains `CONDITIONAL / INSUFFICIENT_SAMPLE`; continue collecting forward observations without changing the frozen candidate.
- Any change to parameters, model logic, CVD mode, or execution assumptions invalidates the prior holdout for that changed candidate and requires a new reserved forward window.

## Required reporting

For every symbol/direction/arm, report:

- exact dataset start/end UTC, 1h/4h row counts, funding event count, taker-flow coverage, missingness, gaps, duplicates, source provenance, and SHA-256 manifests;
- code commit, engine version, full parameter set and canonical parameter hash;
- trade count, wins/losses, win rate, expectancy, PF, net return, max drawdown, max consecutive losses, exposure/time-in-market, average and median holding time;
- each walk-forward fold's return, PF, trade count and drawdown; number and fraction of positive folds;
- base cost and exact 0.30% round-trip cost scenario, with costs applied consistently to entry and all exits; report PF and net return at each;
- largest winner contribution and a recomputed result after removing the single largest winning trade (including recalculated equity curve and drawdown);
- parameter-neighborhood stability using a predeclared local grid (at least +/-10% and +/-20% for continuous parameters where meaningful); report all neighbors, not just successful ones;
- market-regime and calendar-period breakdown to detect concentration in one exceptional event or regime;
- funding timestamp coverage and settlement mark-price provenance. Timestamp alignment alone is not funding cashflow reconciliation.

## Minimum screening gates

A candidate cannot be called production-ready unless all gates pass:

1. Untouched forward holdout net return > 0 and PF > 1 after the specified 0.30% round-trip cost.
2. At least 3 of 4 walk-forward folds positive (or a predeclared majority when fold count differs); aggregate WF PF > 1.
3. The largest-winner-removed result remains positive and PF > 1.
4. The predeclared parameter neighborhood is broadly stable; no isolated single-point optimum.
5. Maximum drawdown <= 15% and maximum consecutive losing trades <= 8, unless stricter asset-specific limits are frozen before testing.
6. At least 100 forward holdout trades per symbol/direction for screening; target 200+ and sufficient representation across multiple months and at least two distinct market regimes. Below this, report insufficient sample, not pass.
7. Profit is not concentrated in one trade, one week, or one exceptional regime. Include block-bootstrap confidence intervals for expectancy and a predeclared multiple-testing adjustment.
8. Funding and execution-cost assumptions have adequate provenance. Unreconciled funding must be explicitly treated as a limitation and cannot be counted as verified edge.

These are minimum gates, not a guarantee of future returns. A separate paper-trading soak and human approval are still required before any production consideration.

## CVD-specific ablation checks

- Confirm `tb` and `tq` exist and are valid for the entire CVD-ON interval; report percentage coverage.
- Verify the 12h/24h proxy uses only closed 1h candles and never reads a later candle or future taker-flow value.
- Compare CVD ON against CVD OFF on the same chronological folds and same cost assumptions.
- Attribute changes in trades, side, entry time, exit time, PF, return and drawdown. A higher score alone is not evidence of value.
- If CVD ON changes candidate selection, choose the mode on development folds only. Do not choose whichever mode wins on the final forward window.

## Engineering work required before running the full matrix

1. Add a side-constrained evaluation mode to `evaluate_hybrid` / `run_v12_backtest`, so LONG and SHORT are simulated independently at signal-generation time.
2. Add a CVD ablation switch that neutralizes only CVD inputs without modifying other features.
3. Add trade-level diagnostics for max consecutive losses, largest-winner removal, monthly/regime concentration, and parameter-neighborhood sweeps.
4. Add a matrix runner that produces one immutable report per symbol/direction/CVD mode and a summary index.
5. Add deterministic unit tests for side isolation, CVD neutrality, cost accounting, holdout immutability, and fail-closed missing-data behavior.
6. Add a reproducible data acquisition/replay job for the five symbols, with exact input manifests and funding audits.
7. Run all checks on the exact PR head. CI success proves code behavior only; it does not prove strategy profitability.

## Safety boundary

- Research only. No exchange order submission.
- Do not modify or merge PR #195, #209, #211, or other existing Draft PRs as part of this work.
- Do not deploy, enable live trading, or configure automatic order execution.
- Entry/exit notifications remain read-only and must explicitly state that no order was placed.
- Production promotion requires a separate explicit human approval after all evidence is reviewed.
