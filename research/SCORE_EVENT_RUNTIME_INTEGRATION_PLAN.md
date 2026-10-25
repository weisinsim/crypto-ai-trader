# Runtime score-event integration plan (research-only)

## Decision

Do not wire persistence into `app.py` in this PR yet. The current dashboard is a market-data and signal-display process, not a realized-trade execution ledger. Its `signal_history` is in-memory and capped; `confirmed_score` is a current confirmed signal field, not proof that a trade was entered or exited. Persisting every confirmed signal as a trade would bias calibration and mislabel signal observations as fills.

## Safe integration stages

### Stage A — observe confirmed signals (future, disabled by default)
- Add a separately configured research-only observer, with persistence disabled unless an explicit local configuration flag is enabled.
- Emit an event only on a genuine transition into a newly confirmed signal; do not emit on every dashboard refresh.
- Stable ID must be deterministic and collision-resistant, based on symbol, direction, timeframe, closed-candle timestamp, and model/version identity. Keep the ID independent of the score value so replayed score drift is reported as a mismatch for the same logical signal rather than as unrelated missing/extra events.
- Freeze the exact score and score-breakdown at confirmation time. The research replay helper now canonicalizes JSON-compatible breakdowns and compares them field-for-field; this is an audit primitive only, not a runtime observer or model replay engine.
- Store the model version, signal timestamp in UTC milliseconds, closed-candle timestamp, source/exchange, score definition/version, and event schema version.
- Persistence errors must be visible in diagnostics. They must not alter signal calculations, signal status, or any execution/risk decision.

### Stage B — realized outcome ingestion (separate source)
- Do not infer a realized trade from a displayed signal, entry/SL/TP proposal, or candle touch alone.
- Accept outcomes only from a defined, auditable source (exchange fills or a deterministic paper-trading simulator) with stable trade/order identifiers and entry/exit timestamps.
- Preserve side, symbol, entry/exit prices, quantity, fees, slippage assumptions, funding cashflow, and exit reason.
- Compute net return using a documented convention; reject outcomes whose costs/funding are missing or unreconciled. Keep paper and exchange fills in separate cohorts.
- Require exact linkage to the frozen signal ID. Reject duplicates, mismatched symbol/side, impossible timestamps, partial fills that have not been aggregated consistently, and orphan outcomes.

### Stage C — calibration and audit
- Generate chronological out-of-sample reports by symbol, direction, model version, and score band.
- Include rejected/filtered signals in coverage diagnostics; do not report only completed winners.
- Report sample size, net expectancy, win rate, profit factor, drawdown, confidence intervals where appropriate, cost/funding coverage, date range, and missing-data rates.
- Keep a sealed holdout untouched during model tuning. Use walk-forward folds and realistic fee/slippage/funding stress tests.
- Treat score 70 as a candidate filter only, not a 70% win probability. Do not promote a model from a score-bucket result alone.

## Acceptance criteria before any runtime wiring

1. Unit and integration tests prove duplicate event idempotency and restart persistence.
2. A fault-injection test proves database failure does not change signal outputs or bypass risk controls.
3. A replay test proves the same closed candle produces the same event ID and frozen score across repeated runs.
4. An audit fixture covers partial fills, missing funding, fees, rejected signals, and orphan outcomes.
5. Runtime observer is research-only, default-off, and has no exchange order API permissions.
6. CI passes on the PR branch. No merge, deployment, or live trading is implied.

## Current status

Implemented research utilities:
- SQLite-backed score-event and realized-outcome store with restart persistence and strict signal-ID linkage.
- Cost-field presence/type coverage diagnostics; presence is explicitly unverified evidence, not proof of correct cost accounting.
- Read-only SQLite integrity audit reporting database integrity, foreign-key violations, orphan outcomes, duplicate-ID groups, and uncompleted score events.
- Unit tests for restart persistence, duplicate rejection, invalid outcomes, incomplete/non-finite cost fields, cost coverage counts, and clean integrity-audit cases.

These utilities are not connected to the dashboard or an outcome source, so no live score/outcome dataset is being collected. The integrity audit checks storage/linkage invariants; it does not verify that a net return reconciles to exchange fills, fees, slippage, or funding. Runtime observer, real model-scoring replay adapter, historical candle replay runner, and fill ingestion remain unimplemented. A first input-preparation utility now validates normalized 1H/4H OHLCV windows, timestamp ordering, finite values, OHLC geometry, and closed-candle boundaries with an injected clock; it is intentionally not a scorer or replay runner. The app previously computed BTC/SOL/AVAX tactical decisions inline in analyze_symbol. Those decision and score rules are now extracted into research/tactical_scorer.py and the live analysis branch calls that same pure function. This is an initial shared scorer seam, not yet a full replay adapter: indicator calculations remain in app.py, XRP still uses its dedicated frozen model, and ETH remains suppressed. Historical inputs are validated separately by research/historical_replay_inputs.py. A deterministic adapter now exists in research/tactical_replay.py for BTC/SOL/AVAX: it accepts frozen closed 1H/4H windows, computes indicators using semantics mirrored from app.py, invokes the shared pure scorer, and returns closed-candle timestamps plus explicit research-only/no-auto-trade flags. Unit tests cover repeatability, exclusion of open candles, unsupported symbols, and insufficient history. This adapter is not yet a complete historical backtest runner: it scores the latest eligible candle per invocation and does not simulate trade lifecycle, fees, slippage, funding, partial fills, or outcomes. The dedicated XRP model is not covered; ETH remains suppressed. Next steps are indicator parity fixtures against the app helpers, then outcome/cost-aware reporting from a clearly defined paper simulator or verified fill source. A chronological orchestrator now exists in research/tactical_replay_series.py: it evaluates each 1H close in order using an injected historical decision time, excludes future 1H/4H candles via the shared frozen-input adapter, and emits observations only. Tests cover ordered unique close timestamps, research-only flags, and invariance of an earlier observation when later 1H candles are mutated. A separate research-only paper lifecycle simulator now exists in research/paper_trade_simulator.py. It assumes entry at the next 1H open, applies configurable adverse slippage and per-side fees, takes 50% at TP1, exits the remainder at TP2/stop/holding limit, and resolves same-bar stop/target ambiguity in favor of the stop. Funding is a configurable fixed adverse cost per 8-hour interval, not historical funding ingestion. Tests cover watch-signal suppression, conservative same-bar handling, partial targets, invalid levels, timestamps, and right-censoring at the end of available data. If future candles run out before the configured holding limit, the simulator labels the outcome CENSORED/DATA_END rather than treating it as a completed max-hold trade; such outcomes must not be included as completed trades in performance summaries. These outputs remain assumption-based: fill prices and costs are explicitly unverified, and this simulator is not wired into the dashboard or any exchange. No dashboard runtime observer, live order wiring, deployment, or profitability claim is implied.


A simulation report builder now exists in `research/paper_backtest_report.py`. It reports completed simulator outcomes separately from right-censored `CENSORED/DATA_END` outcomes, rejects duplicate trade IDs and invalid scores/returns, and summarizes completed simulations by symbol/side and score band. The report deliberately returns no verified calibration result and marks cost/fill verification and out-of-sample purity as unverified, even when input flags are present. Tests cover empty inputs, censored exclusion, duplicate IDs, invalid values, score 100, grouping, and non-completed statuses. This is a reporting layer only; it does not ingest historical market data, prove profitability, verify real fills, or authorize trading.


Follow-up audit hardening: the paper report now distinguishes true right-censored rows (`CENSORED`/`DATA_END`) from other incomplete or non-simulation rows, which are rejected rather than counted as censored. It fails closed if a row claims both a censored exit and `outcome_complete=True`. Dedicated regression tests cover both cases.


Chronological holdout utility added: `research/chronological_holdout.py` partitions rows by the frozen signal timestamp (`signal_ts < cutoff` for development; the exact cutoff belongs to holdout), validates integer millisecond timestamps and exit-after-signal ordering, rejects duplicate trade IDs, and returns sorted disjoint cohorts. Its tests cover the cutoff boundary, trades that exit after the cutoff but were signaled before it, duplicate IDs, invalid timestamps, and empty input. This verifies temporal partition mechanics only; it cannot prove that the holdout was unseen/untuned or that upstream features are free of leakage.


Chronological cohort reporting added in `research/chronological_backtest_report.py`: rows are partitioned by signal timestamp first, then development and holdout simulation reports are generated independently. Tests cover cohort isolation, trades signaled before cutoff but exiting after it, censored holdout coverage, duplicate IDs, invalid timestamps, and empty cohorts. Reports explicitly retain simulation-only status and do not claim holdout secrecy, verified costs/fills, or verified score calibration.


Cost-sensitivity stress runner added in `research/paper_stress_test.py`. It replays the same frozen candidate set under named base/moderate/high fee, slippage, and funding-cost assumptions, reporting each scenario independently. It keeps right-censored outcomes out of performance metrics and explicitly marks historical funding, fills, costs, and calibration as unverified. This is sensitivity analysis, not evidence of actual exchange costs and not permission to tune on a holdout.


Execution-cost audit refinement: the paper lifecycle simulator now applies adverse slippage to both entry and every exit fill (long exits lower, short exits higher), while retaining the reference trigger price separately for audit. Regression tests verify exit slippage worsens returns for both LONG and SHORT. These remain modeled fills, not order-book or exchange-fill verification.


Look-ahead boundary hardening: the paper simulator now rejects any future candle whose timestamp is less than or equal to the signal candle close. This prevents accidental reuse of the signal candle as the post-signal entry/outcome candle. Regression tests cover the exact-boundary rejection; this validates timestamp ordering only and does not prove the upstream candle timestamps themselves are correctly defined.
