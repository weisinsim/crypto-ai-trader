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
