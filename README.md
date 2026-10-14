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
