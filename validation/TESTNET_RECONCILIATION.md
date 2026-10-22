# Testnet state reconciliation gate

`validation/testnet_reconciliation.py` compares expected signed positions with normalized, read-only testnet snapshots. It fails closed for missing/failed snapshots, invalid quantities, quantity mismatches beyond the configured tolerance, unexpected non-zero positions, unexpected open orders, duplicate order identifiers, and missing/ambiguous/non-reduce-only/non-open protective orders explicitly listed by the caller.

Example:

```python
from validation.testnet_reconciliation import reconcile_testnet_state

result = reconcile_testnet_state(
    expected_positions={"BTCUSDT": "0.5", "ETHUSDT": "0"},
    snapshots={
        "BTCUSDT": {
            "ok": True,
            "position_amount": "0.5",
            "open_orders": [{
                "client_order_id": "btc-stop-1",
                "status": "NEW",
                "reduce_only": True,
            }],
        },
        "ETHUSDT": {
            "ok": True,
            "position_amount": "0",
            "open_orders": [],
        },
    },
    required_protective_order_ids={"BTCUSDT": ["btc-stop-1"]},
)
if not result["ok"]:
    # Halt the testnet run and investigate; do not auto-correct blindly.
    print(result["reasons"])
```

## Integration checklist

- Obtain position and open-order snapshots from the testnet adapter; do not fabricate a successful snapshot when a request fails.
- Validate snapshot freshness and completeness separately before passing snapshots to this pure helper.
- Reconcile all relevant symbols and account exposure, not just the symbol that most recently generated a signal.
- Stop the testnet run on any failed reconciliation. Do not automatically submit, cancel, or replace orders based only on this helper's result.
- Persist the reconciliation result with timestamps and enough context to audit restart/recovery behavior.

The helper is a pure decision function. It does not fetch data, submit/cancel orders, or establish snapshot freshness/completeness. Callers must obtain authoritative snapshots from the testnet adapter, check timestamps/health separately, and halt the soak test on any non-empty reason list. This is not live-trading authorization and must not be used as a substitute for exchange-side reconciliation or production controls.
