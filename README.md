# Crypto AI Trader

Asset-adaptive cryptocurrency market monitoring and strategy research.

## Local validation

```bash
python -m unittest discover -s tests -v
python -m compileall -q app.py validation tests
```

GitHub Actions runs the checks on pushes, pull requests, and manual dispatch.

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

The audit reports out-of-range and nonmatching timestamps and exits with a report status of `REVIEW_REQUIRED` when it detects alignment issues. The optional `--max-gap-hours 12` flags event intervals longer than the operator-selected threshold for review; funding cadence can change, so this is a review trigger, not proof of missing events. It does not alter timestamps or assert complete event coverage.

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
