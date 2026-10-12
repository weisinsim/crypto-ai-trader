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

## Re-run evidence from historical research run #160 (2026-10-12)

The five-symbol 1H historical workflow completed successfully, including the funding coverage/provenance audit gate. AVAX follow-up diagnostics were rerun after restricting parameter-neighborhood funding events to the training window. This is a causal-boundary correction; it does not change the frozen holdout performance or grant promotion.

### Chronological holdout halves

| Holdout segment | Cost scenario | Trades | Net return | Max drawdown | Profit factor |
|---|---|---:|---:|---:|---:|
| First half | Base | 20 | +8.3119% | 3.7499% | 1.9115 |
| First half | 0.30% round-trip | 20 | +5.9438% | 4.2102% | 1.5900 |
| First half | 0.46% round-trip | 20 | +3.6248% | 4.6689% | 1.3292 |
| Second half | Base | 46 | +2.5922% | 8.2511% | 1.0925 |
| Second half | 0.30% round-trip | 46 | -1.7737% | 9.5949% | 0.9403 |
| Second half | 0.46% round-trip | 46 | -5.9584% | 11.5667% | 0.8098 |

### Profit concentration

Top winning-trade contribution as a share of net holdout profit was modest in both halves; the failure is not explained solely by one outlier trade. In the first half, top 1 / top 3 / top 5 shares were 8.81% / 26.23% / 43.45% at base costs. In the second half they were 4.99% / 14.81% / 24.55%. Concentration diagnostics are descriptive, not a substitute for the cost and stability gates.

### Predeclared parameter-neighborhood results

| Variant | Trades | Base return / PF | 0.30% return / PF | 0.46% return / PF |
|---|---:|---:|---:|---:|
| Window -12 | 87 | +6.8174% / 1.1327 | -3.1065% / 0.9428 | -12.1192% / 0.7870 |
| Window +12 | 63 | +14.0721% / 1.4100 | +6.4272% / 1.1747 | -0.7138% / 0.9817 |
| Entry z -0.25 | 85 | +11.8731% / 1.2403 | +1.6373% / 1.0313 | -7.6731% / 0.8603 |
| Entry z +0.25 | 67 | +17.5938% / 1.4795 | +9.1805% / 1.2342 | +1.3598% / 1.0328 |
| Stop ATR -0.25 | 67 | +17.5938% / 1.4795 | +9.1805% / 1.2342 | +1.3598% / 1.0328 |
| Stop ATR +0.25 | 62 | +13.3309% / 1.3809 | +6.7600% / 1.1819 | +0.5636% / 1.0144 |

The neighborhood is mixed: some variants retain positive severe-cost results, while others fail materially. The selected full-holdout strategy itself fails the 0.46% cost gate, and the second holdout half fails at both stress levels. Do not choose a neighbor based on these holdout outcomes; any new parameter set requires a later untouched interval.

### Funding boundary and final decision

Run #160 uses 1,095 historical funding settlements for AVAX. Parameter-neighborhood diagnostics now restrict funding events to settlements at or before the last training candle. The historical workflow's funding audit gate passed for all five symbols.

**Decision: AVAX mean reversion remains RESEARCH ONLY / NOT APPROVED.** Keep the holdout sealed for model selection. Do not promote or tune against this same holdout; next valid confirmation must use a later untouched time interval with the same cost gates.

