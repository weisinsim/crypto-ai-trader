# Crypto AI Trader

Asset-adaptive cryptocurrency market monitoring and strategy research.

## Local validation

```bash
python -m unittest discover -s tests -v
python -m compileall -q app.py research validation tests
```

The compile check includes the research pipeline as well as the application and validation modules. GitHub Actions runs the checks on pushes, pull requests, and manual dispatch.

## Historical data and baseline backtest

Fetch a UTC date range (the end date is exclusive), then run the baseline with the matching interval:

```bash
python -m research.binance_data --symbol BTCUSDT --interval 1h --start 2025-01-01 --end 2026-01-01 --output data/BTCUSDT_1h.csv
python -m research.run_backtest --csv data/BTCUSDT_1h.csv --symbol BTCUSDT --interval 1h --output reports/BTCUSDT_1h_baseline.json
```

The runner rejects missing or irregular candle timestamps. Reports are marked `RESEARCH_ONLY`; the EMA baseline is a software/data-pipeline smoke test, not an optimized or approved trading model. The current funding input is a constant assumption, not historical realized funding.

Download Binance USD-M historical funding events for a UTC range (end date exclusive) and pass the resulting CSV to the backtest:

```bash
python -m research.binance_funding_data --symbol BTCUSDT --start 2025-01-01 --end 2026-01-01 --output data/BTCUSDT_funding.csv
python -m research.run_backtest --csv data/BTCUSDT_1h.csv --symbol BTCUSDT --interval 1h --funding-csv data/BTCUSDT_funding.csv --output reports/BTCUSDT_1h_funding.json
```

The downloader retains Binance's reported `markPrice` field and labels its source. Its output is `DOWNLOADED_NOT_RECONCILED`: successful retrieval does not prove complete historical coverage, contract/account eligibility, or agreement with realized account cashflows. Review `validation/FUNDING_SETTLEMENT_RECONCILIATION.md` before interpreting funding PnL. Do not use synthetic fixtures as market data.

Before running a funding-aware backtest, run the standalone timestamp audit to inspect misaligned events without forcing them onto candle timestamps:

```bash
python -m research.audit_funding_data --candles data/BTCUSDT_1h.csv --funding-csv data/BTCUSDT_funding.csv --symbol BTCUSDT --interval 1h --max-gap-hours 12 --output reports/BTCUSDT_funding_audit.json
```

The audit reports out-of-range and nonmatching timestamps and exits with a report status of `REVIEW_REQUIRED` when it detects alignment issues. When the CSV loader has both `funding_time_raw` and a normalized `funding_time`, the audit uses `funding_time_raw` so boundary normalization cannot hide raw timestamp misalignment; backtest settlement calculations may still use the normalized field. The optional `--max-gap-hours 12` flags event intervals longer than the operator-selected threshold for review; funding cadence can change, so this is a review trigger, not proof of missing events. The audit does not alter timestamps or assert complete event coverage. It also flags duplicate raw funding-event timestamps as `REVIEW_REQUIRED`, reports the duplicated timestamps and excess records, and includes zero-length intervals in diagnostics; investigate duplicates before using funding PnL.

The backtest report's `funding_data` section includes timestamp diagnostics: number of events in the candle range, exact candle-open timestamp matches, non-matching event timestamps, observed event-interval range, and source/venue labels. These are diagnostic counts only. Exact timestamp matches do not prove that every funding event is present, that reported mark prices are independently authenticated, or that a position qualified for settlement. The report therefore keeps `event_completeness_verified` false; reconcile source coverage and account cashflows independently before treating funding PnL as authoritative.


## Live-trading safety gate (pre-trade only)

The current live order gate is deliberately fail-closed and is not an exchange execution adapter. It approves or rejects a proposed order snapshot; it never sends orders or manages an exchange account. Use it only after collecting authoritative, fresh inputs from the exchange and portfolio/risk service:

```python
from validation.promotion_gate import evaluate_live_order_gate

decision = evaluate_live_order_gate(snapshot, limits)
if not decision["approved"]:
    # Do not submit; record decision["reasons"] and remain flat.
    pass
```

The snapshot must include exchange health, kill-switch state, confirmed closed-candle status, model status, actionable direction, market/analysis age, spread, risk fraction, daily loss, open-position count and requested notional. Limits must be explicitly configured by the operator. Missing, non-finite or out-of-limit values fail closed. Only a model explicitly marked `PRODUCTION` can pass this gate; current research models are not thereby promoted. A passing decision is **not** evidence of profitability or permission to connect order submission automatically.

Before enabling exchange execution, separately implement and validate idempotent order handling, exchange position reconciliation, reduce-only protective exits, partial-fill recovery, precision/minimum-notional checks, rate-limit/reconnect handling, persistent kill switch, daily loss stop, and testnet/paper soak with signal/replay parity. Never put API keys in source control.


### Paper order lifecycle

`validation/paper_execution.py` provides an isolated simulator for idempotent order creation, partial/full fills, cancellation and conservative stop/target exits (stop wins when both are touched in one OHLC candle). By default it is in-memory; pass `storage_path="data/paper-ledger.json"` to enable versioned JSON persistence (schema v3) with atomic file replacement, restart recovery, fill-event idempotency via `record_fill(..., fill_id="...")`, and fail-closed handling of unreadable/unknown-schema ledgers. Schema-v1 and schema-v2 ledgers are migrated on the next save; legacy fill-event history is preserved as incomplete because older fills cannot be deduplicated retroactively. Schema v3 records whether a partial order remainder was cancelled, and protective exits remain available for the already-filled exposure after cancellation. Mutations are rolled back in memory if persistence fails. Persistence is intended for a single-process paper test only; it is not a distributed lock, exchange reconciliation mechanism, or production trading ledger. No exchange is contacted and no real orders are placed. Fees, funding, exchange precision and network recovery are not modeled. `validation/paper_reconciliation.py` derives signed and gross exposure from paper ledger orders, including partially filled cancelled orders; it is not a substitute for reconciling authoritative exchange positions. Do not use this in place of a production execution adapter.

### Daily loss guard (paper/research only)

`validation/daily_loss_guard.py` tracks realized-PnL events against an explicit UTC date and a configured positive loss limit. At or below the loss threshold it locks new entries for that UTC day; the lock remains in place even if later PnL recovers, and duplicate event IDs are idempotent. Optional JSON persistence supports restart recovery and fails closed on unreadable or unsupported ledgers. The caller must supply authoritative realized PnL (including fees/funding if desired); this module does not connect to exchanges, calculate unrealized PnL, coordinate multiple processes, or independently block any order route. Integrate it as one input to a fail-closed entry gate; do not treat it as production risk infrastructure.

`validation/guarded_order_gate.py` composes the existing pre-trade decision helper with the daily-loss lock and adds `PERSISTENT_DAILY_LOSS_LOCKED` when the day is locked. This is testable decision logic only, not a guarantee that every caller uses it and not an order-submission mechanism. Any future execution adapter must enforce one centralized, fail-closed gate and reconcile actual exchange state before any production use.

## Funding-data interpretation gate

The backtest report includes `funding_interpretation_gate` and `funding_data.audit_status`. Missing funding history, duplicate raw event timestamps, events outside the candle coverage, or raw event times that do not align to candle opens set the gate to `FUNDING_DATA_REVIEW_REQUIRED` / `REVIEW_REQUIRED`. A clean timestamp-alignment diagnostic is only `TIMESTAMP_ALIGNMENT_ONLY_NOT_RECONCILED`; it does not prove complete venue history, source authenticity, or account cashflow reconciliation. The gate is an interpretation warning, not a claim that funding is fully audited and not a production approval.

## Research status and promotion criteria

A passing unit test or successful compile validates software behavior only; it is not evidence of strategy profitability. Do not promote any model to production without a reproducible, independently reviewable report that documents:

- complete, timestamp-checked historical data and pagination;
- fixed train, validation, and untouched holdout date ranges;
- realistic fees, slippage, funding, and execution assumptions;
- protection against look-ahead bias and use of closed candles;
- trade count, expectancy, profit factor, maximum drawdown, and exposure;
- walk-forward results and parameter-neighborhood stability;
- coin-specific and long/short results, where supported.

A model's FROZEN status means its parameters are preserved; it does not imply that profitability has been proven. Confirmed execution signals require an explicit validation record.


### Paper portfolio consistency gate

`validation/portfolio_order_gate.py` composes the pre-trade gate and persistent daily-loss guard with paper-ledger reconciliation. It rejects malformed ledger state and rejects a snapshot when its supplied open-position count disagrees with active symbols derived from gross filled exposure. Opposing long/short legs on one symbol still count as an active symbol even if net quantity is zero. This guards against stale paper snapshots; it is not authoritative exchange reconciliation and must not be used to authorize live orders.


The pre-trade screening helpers reject non-mapping inputs, boolean-as-number values, negative market age/spread metrics, and negative daily-loss fractions. Candidate screening is only a preliminary filter: positive development/holdout metrics and a sufficient trade count do not establish statistical significance or authorize production use. Portfolio consistency checks operate only on paper-ledger records and must never replace exchange-side position/order reconciliation.


## Execution status and safety gates

The dashboard currently generates monitoring/research signals; it does not place orders. The
`execution.safety` module is a fail-closed validation layer for proposed intents, not an
exchange connector. Its default mode is paper, and it rejects any request that attempts to
enable live trading. Do not add exchange API keys to source control or expose keys with
withdrawal permissions.

Before any real-money adapter can be considered, complete and archive all of these gates:

1. Full historical replay parity against the frozen baseline, including signal time, direction,
   entry, stop, and targets for each supported symbol.
2. Benchmark the full replay on identical data and environment; record runtime and memory.
3. Verify order sizing, tick/step-size rounding, minimum notional, partial fills, duplicate-order
   protection, reduce-only exits, reconnect/retry handling, and idempotency on exchange testnet.
4. Exercise kill-switch, stale-signal rejection, daily loss limits, max position/notional limits,
   and restart recovery in testnet/paper mode.
5. Review chronological holdout results after fees and historical funding. A completed pipeline
   is not evidence of profitability.
6. Require an explicit operator decision before any live mode is implemented or enabled.

Current state: `RESEARCH_ONLY`; real-money order placement is intentionally unavailable.

The Binance USD-M TESTNET adapter now includes `submit_testnet_market_order(..., client_order_id, config)`, which can submit a market order only to the hard-coded testnet host after safety and exchange-filter checks. It uses Binance's `newClientOrderId` for exchange-side request identity, but this alone is not a durable idempotency/reconciliation system: ambiguous network outcomes must be reconciled against exchange order history before retrying. This function does **not** attach protective stop-loss/take-profit orders, set leverage, or manage fills/positions. Do not use it as a standalone trading bot; it is for controlled testnet integration only. The adapter also has `query_testnet_order(symbol, client_order_id)`, a signed read-only lookup to reconcile an order's status and executed quantity without resubmitting it. A production-quality retry/recovery worker and protective-exit lifecycle remain outstanding. The adapter now also exposes `testnet_position_snapshot(symbol)` to read the signed account position and open orders for one symbol in a single diagnostic result; it is read-only and does not itself reconcile, cancel, or repair mismatches.


### Testnet protective-exit orchestration

`execution/testnet_lifecycle.py` provides `ensure_testnet_protective_exits(...)`: it reads the current testnet position and open orders, validates stop/target geometry, reuses visible matching reduce-only conditional orders, rejects duplicates or price mismatches, and requests the stop-loss before the take-profit. If the target request fails after the stop succeeds, it returns an explicit partial-state result requiring reconciliation. This is still not a complete live-safe lifecycle: exchange responses can be ambiguous, position size can change between requests, sibling exits are not cancelled after a fill, and a post-submit snapshot is mandatory. The function is TESTNET orchestration only; it cannot enable production endpoints.


The protective-exit orchestrator now performs a second authoritative testnet snapshot after both requests and verifies that the position and entry price did not change, exactly one active reduce-only stop and target exist, and both orders have the expected side, trigger, and full current position quantity. Any mismatch fails closed and requires manual/external reconciliation. This narrows race and silent-rejection risk but does not solve partial fills, atomic OCO/sibling cancellation, or ambiguous network outcomes.


The testnet adapter now supports cancelling one explicitly identified order by `origClientOrderId` (not bulk symbol cancellation). `cancel_testnet_sibling_after_flatten(...)` only requests sibling cancellation after the primary exit is confirmed `FILLED` with positive executed quantity and a fresh snapshot confirms the position is flat; partial fills and non-flat positions leave the sibling in place. It then checks cancellation acknowledgement and takes another position snapshot. This is a callable recovery primitive, not a continuous fill monitor; it cannot eliminate exchange/network races and must be exercised on testnet before operational use.


### Runtime risk gate (paper/testnet only)

`execution/runtime_guard.py` adds a fail-closed runtime gate for an execution worker to call before each new entry. It blocks missing/stale account snapshots, an emergency-stop flag, daily realized-loss threshold, concurrent-position and notional limits, and repeated execution errors. The defaults are conservative placeholders (10 USDT daily realized loss, one open position, 100 USDT symbol/total notional, three consecutive execution errors). A production runner must provide authoritative and freshly reconciled state and persist the daily loss/error counters; this module does not itself persist state, calculate unrealized PnL, or submit/cancel orders. It is not yet wired into a continuously running order worker and is not live-trading approval.


### Guarded TESTNET entry flow

`execution/testnet_entry_flow.py` connects the intent gate, runtime kill-switch/risk gate, signed account preflight, a single explicitly identified TESTNET market-order submission, order/position reconciliation, and protective-exit verification. It never targets production and never automatically retries an ambiguous market-order request. If an entry appears to have opened exposure but protection cannot be verified, it returns an explicit reconciliation-required result. This is still a single-pass callable workflow, not a persistent supervisor: exchange-state races, partial fills, process restarts, daily-PnL persistence, and ongoing protective-order monitoring remain unresolved. Do not run against real funds or treat testnet success as evidence of strategy profitability.


### TESTNET restart recovery

`execution/testnet_recovery.py` adds an explicit recovery entry point for a process restart: it reads the authoritative TESTNET position snapshot, refuses invalid/stale-unavailable state and invalid stop/target geometry, and verifies or installs protective exits for an existing position. It never submits a new entry order. It is a callable recovery primitive, not a continuously running supervisor or durable event ledger; the operator must supply the intended stop/target levels and manually reconcile ambiguous states. Production trading remains disabled.


### Durable runtime risk state

`execution/runtime_state_store.py` persists UTC-day realized-PnL events, exchange-derived exposure metrics, consecutive execution errors, and the emergency stop flag in SQLite. State survives process restarts; missing account metrics return no snapshot and therefore must block entries. Callers must refresh exposure from an authoritative exchange snapshot, deduplicate realized-PnL fill events, and check snapshot freshness before entry. This local database is not distributed coordination and does not replace a live supervisor, fill stream, reconciliation loop, or independent account-level risk checks.



### Single-tick TESTNET supervisor

`execution/testnet_supervisor.py` now composes signed account preflight, fresh gross exposure metrics from the TESTNET account, SQLite risk-state refresh, the fail-closed runtime gate, existing-position protection recovery, and the guarded entry workflow. Call `TestnetSupervisor.run_once(...)` from an externally supervised process; this module intentionally does not run an unattended loop or generate signals. It never submits an entry if account metrics are missing, account preflight fails, the persisted kill switch/risk gate blocks, or an existing position lacks explicit recovery stop/target metadata. A supplied entry is submitted at most once by the guarded flow; ambiguous outcomes are not retried.

The supervisor depends on authoritative account-position `notional` fields and fails closed when an open position lacks them. Its local SQLite store is single-host state, not distributed locking. Exchange refresh and order submission are still separate operations with race windows; this is a TESTNET integration milestone, not production approval. Keep keys restricted to TESTNET and do not configure production credentials. Real-money execution remains disabled.



### Durable one-shot order reservation

The TESTNET supervisor now reserves each supplied `client_order_id` in SQLite before handing it to the guarded entry workflow. The unique key survives process restarts and prevents accidental second submission under the same ID; outcome metadata records completed, rejected, submitted, or reconciliation-required states. A reserved ID is intentionally never released automatically, including when the outcome is uncertain. Operators must reconcile an uncertain attempt against the exchange before creating a new, explicitly reviewed attempt. This local journal is not a distributed lock and does not prevent another process or a different client ID from submitting an economically duplicate order.
