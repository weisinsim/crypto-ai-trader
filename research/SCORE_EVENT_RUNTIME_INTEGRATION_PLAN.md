# Runtime score-event integration plan (research-only)

## Decision

Do not wire persistence into `app.py` in this PR yet. The current dashboard is a market-data and signal-display process, not a realized-trade execution ledger. Its `signal_history` is in-memory and capped; `confirmed_score` is a current confirmed signal field, not proof that a trade was entered or exited. Persisting every confirmed signal as a trade would bias calibration and mislabel signal observations as fills.

## Safe integration stages

### Stage A — observe confirmed signals (future, disabled by default)
- Add a separately configured research-only observer, with persistence disabled unless an explicit local configuration flag is enabled.
- Emit an event only on a genuine transition into a newly confirmed signal; do not emit on every dashboard refresh.
- Stable ID must be deterministic and collision-resistant, based on symbol, direction, closed-candle timestamp, and model/version identity. Repeated processing of the same closed candle must be idempotent.
- Freeze the exact score and score-breakdown at confirmation time. Never reconstruct the score from later candles or trade outcomes.
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

Only the local SQLite store and calibration helper exist. They are not connected to the dashboard or an outcome source, so no live score/outcome dataset is being collected. This document specifies a safe next implementation path; it does not claim any of the stages above are implemented.
