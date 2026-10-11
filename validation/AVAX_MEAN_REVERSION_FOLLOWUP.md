# AVAX Mean-Reversion Follow-up — Research Gate

Status: RESEARCH ONLY. No production approval.

## Evidence from run #80

The symbol-specific frozen mean-reversion parameters were tested on 8,760 hourly bars with a chronological 60/40 train/holdout split. The full holdout reported:

| Cost scenario | Trades | Net return | Max drawdown | Profit factor |
|---|---:|---:|---:|---:|
| Base | 66 | +11.1196% | 8.2511% | 1.2818 |
| 0.30% round-trip stress | 66 | +4.0647% | 9.5949% | 1.0978 |
| 0.46% round-trip severe | 66 | -2.5496% | 11.5667% | 0.9414 |

The second holdout half alone was negative at both stressed cost levels: -1.7737% at 0.30% and -5.9584% at 0.46%. Therefore, AVAX is a research lead only, not a production candidate.

## Required follow-up

1. Preserve the run #80 holdout as sealed evidence; do not tune parameters against it.
2. Use training data only to define any market-regime labels and thresholds. Report trend/range and volatility buckets without selecting the best bucket after looking at holdout outcomes.
3. Audit contribution concentration: report the top 1, top 3, and top 5 trade contributions; rerun diagnostics after removing the largest winning trade and largest three winning trades.
4. Run a predeclared parameter-neighborhood test around the selected parameters. Report all neighboring results, not just the best one.
5. Report first-half and second-half holdout metrics separately under base, 0.30%, and 0.46% costs.
6. Confirm funding mark-price provenance and settlement-event coverage before treating the result as exchange-realistic.
7. Any revised candidate must be evaluated on a later, untouched time interval before promotion.

## Promotion gates

- Positive net return and profit factor > 1 at both 0.30% and 0.46% round-trip cost.
- No single trade or small handful of trades explains most of the total profit.
- Stable results across predeclared subperiods and neighboring parameters.
- Funding-event and mark-price provenance limitations explicitly reconciled.
- A fresh, untouched validation interval passes the same gates.

If any gate fails, keep status RESEARCH or REJECT. Do not weaken thresholds to force a passing model.
