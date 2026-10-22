# Replay cost and portfolio-risk audit

These utilities are research controls, not strategy approval and not an order-execution system.

## Exit audit costs

Run `validation/exit_parameter_audit.py` with candle and signal CSVs. The defaults model round-trip fees at 10 bps and a separate adverse round-trip slippage proxy at 5 bps. These are explicit sensitivity assumptions, not measured fills. A timestamped funding file is treated as event cashflows using the entry-price proxy; events in `(entry_signal_close, exit_candle_close]` are charged, including the exit boundary conservatively.

The JSON report includes per-trade gross R, fee R, slippage R, funding R, and net R. Summary fields report aggregate components, `net_R_reconciliation_error_sum`, and `max_abs_trade_reconciliation_error`. The per-trade maximum prevents positive and negative discrepancies from cancelling in an aggregate sum; both should be zero (up to floating-point rounding). The portfolio adapter explicitly marks empty trade evidence as `NO_TRADES_TO_AUDIT` and keeps strategy approval, profitability claims, and live trading disabled. Funding settlement reconciliation against exchange records is not complete, so the report does not qualify as proof of profitability.

## Multi-symbol portfolio stop-risk audit

First produce one exit-audit JSON per symbol. Then combine the selected development or holdout trade evidence. Example:

```bash
python validation/portfolio_risk_from_exit_audits.py \
  --audit BTCUSDT=reports/btc_exit_audit.json \
  --audit ETHUSDT=reports/eth_exit_audit.json \
  --period holdout \
  --target target_2.5R \
  --trade-set signal_level \
  --risk-pct-per-trade 0.5 \
  --max-portfolio-risk-pct 2.0 \
  --max-symbol-risk-pct 1.0 \
  --out reports/portfolio_risk_audit.json
```

Use `--trade-set single_position` for each symbol's non-overlapping sequence. `signal_level` retains overlapping trades and is useful for auditing the potential aggregate exposure; it can count trades that a separate capital/position allocator might reject. The per-trade risk percentage is deliberately required and explicitly supplied; it is not inferred from R-multiples or historical returns.

Portfolio risk limits (2.0% portfolio and 1.0% per symbol by default) are configurable research guardrails, not empirically optimized settings. The audit models planned stop risk only. It does not model correlation shocks, gaps through stops, liquidation, exchange outages, actual fills, or dynamic equity compounding. Keep live order execution disabled until those controls and the exchange settlement/fill reconciliation have been independently validated.
