# Crypto AI Trader

Asset-adaptive cryptocurrency market monitoring and strategy research.

## Local validation

```bash
python -m unittest discover -s tests -v
python -m compileall -q app.py validation tests
```

GitHub Actions runs the checks on pushes, pull requests, and manual dispatch.

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
