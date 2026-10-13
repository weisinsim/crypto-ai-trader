# Historical Baseline Research Audit — 2026-10-13

## Scope and provenance

- Repository revision: `666862af876ec783302ea7f80af21d21b3f8b178`
- Workflow: [Historical baseline research run 38379994375](https://github.com/weisinsim/crypto-ai-trader/actions/runs/38379994375)
- Data scope: 1H closed-candle history and historical funding; the workflow's funding audit gate passed for all five symbol jobs.
- This is research-only evidence. The tested baseline/diagnostic strategies are not the frozen XRP V27.2 production candidate and do not authorize live trading.
- Drawdown accounting was updated to track the close-equity peak while measuring troughs against adverse intrabar OHLC marks. This is conservative bar-level estimation, not tick-accurate intrabar reconstruction.

## Full-holdout baseline summary

Metrics below are from the frozen-parameter stability diagnostic's full holdout. Cost scenarios are base, 0.30% round-trip stress, and 0.46% severe round-trip stress.

| Symbol | Trades | Base return | Base max DD | Base PF | +0.30% return / PF | +0.46% return / PF |
|---|---:|---:|---:|---:|---:|---:|
| BTCUSDT | 61 | -9.7117% | 17.4377% | 0.7778 | -18.8186% / 0.6028 | -27.0240% / 0.4691 |
| ETHUSDT | 16 | -3.2748% | 4.7910% | 0.7323 | -5.7528% / 0.5776 | -8.1723% / 0.4542 |
| SOLUSDT | 15 | -6.4917% | 9.9206% | 0.4588 | -8.2143% / 0.3825 | -9.9075% / 0.3208 |
| AVAXUSDT | 66 | +11.1195% | 8.2474% | 1.2819 | +4.0646% / 1.0978 | -2.5497% / 0.9413 |
| XRPUSDT | 10 | +0.9567% | 5.4313% | 1.1443 | -0.2073% / 0.9716 | -1.3594% / 0.8295 |

## Interpretation

1. No symbol passes a robust all-costs profitability screen in this run. AVAX is positive at base and 0.30% cost, but turns negative at 0.46%; XRP is only marginally positive before stress and turns negative at 0.30%. BTC, ETH, and SOL are negative at base cost.
2. The XRP diagnostic has only 10 full-holdout trades, which is too small to support a reliable performance claim. The frozen stability diagnostic's same-holdout direction comparisons are descriptive only and must not be used to select or change direction.
3. These results are a warning against promotion, not proof that every possible strategy fails. Any candidate needs pre-registered selection, chronological walk-forward and independent holdout, transaction/funding costs, tail-risk and parameter-stability checks.
4. Keep the frozen XRP V27.2 parameters unchanged: XRP-only, 4H regime + 1H confirmation, SL 1.7 ATR, TP2 2.8 ATR, kill-switch and shock filter, with NO-TRADE as default. Do not infer that the separate 1H diagnostic is the same strategy.

## Validation status

- The five symbol jobs completed successfully and uploaded raw inputs plus JSON reports as artifacts in the linked workflow.
- A successful workflow means the research jobs completed; it does **not** mean a model passed profitability or production certification gates.
- Next step: review the full JSON artifacts, reconcile the baseline and walk-forward summaries with the stress results, and retain a research-only / no-live-trading status until all required gates pass.
