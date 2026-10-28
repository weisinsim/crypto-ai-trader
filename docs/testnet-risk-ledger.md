# Testnet Risk Ledger and Entry Kill Switch

This control is a **testnet-only safety guard**, not production trading approval.
The adapter has no production endpoint, and this document does not authorize live trading.

## Required durable ledger

Set `CRYPTO_TESTNET_RISK_LEDGER_PATH` to an absolute path on durable storage before constructing the adapter, for example:

```bash
export CRYPTO_TESTNET_RISK_LEDGER_PATH="/var/lib/crypto-ai-trader/testnet-risk.sqlite3"
```

Every process that may submit entries for the same testnet account must use the **same database file** on storage with working SQLite locking semantics. Do not use a per-process temporary directory, ephemeral container filesystem, or separate database per worker. If the ledger cannot be initialized or read, entry submission fails closed.

The ledger uses SQLite WAL mode and full synchronous durability. Back up the database using a SQLite-consistent backup procedure. Never delete or reset it to clear a stuck reservation; reconcile the order with the exchange first.

## Kill switch behavior

A new ledger initializes with the persistent entry kill switch **active**. While active, market-entry submission is blocked before any exchange request. A database read/update failure also blocks entries.

Inspect the state:

```python
import os
from execution.risk_ledger import SQLiteRiskLedger

ledger = SQLiteRiskLedger(os.environ["CRYPTO_TESTNET_RISK_LEDGER_PATH"])
print(ledger.kill_switch_status())
```

Activate the switch immediately to block new entries:

```python
ledger.set_kill_switch(True, reason="operator emergency stop")
```

Clearing the switch requires an explicit operator action and a non-empty audit reason. Do this only after verifying the TESTNET account, credentials, risk configuration, order reconciliation status, and monitoring. The API call changes a persistent control record; it does not certify the system as safe or enable production trading.

```python
ledger.set_kill_switch(False, reason="operator reviewed testnet readiness checklist")
```

The adapter reads the persistent switch before sending a market entry. To ensure every entry path is protected, all future adapters and orchestration code must use the same ledger and must not bypass this guard.

## Reservation states and recovery

- `RESERVED`: capacity allocated before order submission.
- `SUBMITTING`: the exchange POST is about to be attempted.
- `UNKNOWN`: the outcome may be ambiguous; do not blindly retry.
- `EXTERNALIZED`: reconciliation has established that the order is visible on the exchange. **The reservation still consumes local risk capacity** because an open entry can still fill; exchange visibility alone does not mean the exposure has disappeared.
- `RELEASED`: the reservation no longer consumes reserved capacity.

Uncertain outcomes intentionally retain capacity. A `RESERVED` record can be released only with a `confirmed_pre_submit:` detail prefix after independently establishing that submission never began; `SUBMITTING`/`UNKNOWN`/`EXTERNALIZED` releases require `confirmed_terminal:` evidence. Do not manually transition an ambiguous reservation based on a timeout alone. The ledger's explicit confirmation prefixes are guardrails against accidental transitions, not cryptographic proof of reconciliation.

## Recovery inventory API

The ledger provides `list_unresolved(limit=100)` to enumerate `RESERVED`, `SUBMITTING`, `UNKNOWN`, and `EXTERNALIZED` records oldest-first in bounded batches. All four states continue consuming local risk capacity until a permitted release transition reaches `RELEASED`. It is read-only and does not infer whether a POST reached the exchange. Recovery tooling should use this inventory to schedule per-order reconciliation, while retaining unresolved reservations on any error. The inventory API alone is not an automated recovery worker.

## Per-order reconciliation primitive

The adapter exposes `reconcile_testnet_entry_reservation(symbol, client_order_id)` for a deliberately narrow, read-only reconciliation step. It first queries the exchange order by client ID, then obtains a bounded position/open-order snapshot. It does not place, cancel, retry, or modify orders.

- A `NEW` or `PARTIALLY_FILLED` entry is marked `EXTERNALIZED` only when the open-order snapshot contains exactly one matching client ID and its status and quantities agree. This state does not free local capacity; the order may still fill.
- A `CANCELED`, `REJECTED`, or `EXPIRED` order is released only when the exchange reports zero executed quantity and the open-order snapshot does not contain it.
- Filled orders, terminal orders with any fill, missing/mismatched snapshots, stale snapshots, HTTP/transport failures, and other ambiguous states remain unresolved and require further reconciliation.
- The exchange order query and account/open-order snapshot are separate non-atomic reads. This helper is a primitive, not a complete recovery worker or fill ledger; it must not be treated as unattended operational readiness.

## Durable recovery audit

Each per-order outcome produced by the bounded recovery sweep is appended to the SQLite `recovery_audit` table, including order ID, supplied symbol (if any), state before the attempt, outcome category, and a bounded reason code. Use `SQLiteRiskLedger.list_recovery_audit(limit=100)` to read the newest events; the read is bounded and does not mutate reservations. The sweep reports audit-write failures explicitly and marks those outcomes as requiring reconciliation. Audit records help trace attempts but do not prove exchange state, replace backups, or establish an immutable/tamper-proof audit trail.

## Important limitations

SQLite reservation transactions are atomic only among cooperating processes sharing the same database. They cannot atomically commit with the exchange's order API. A crash between database reservation and POST, or after POST but before recording the response, requires exchange reconciliation by client order ID.

A complete unattended recovery/reconciliation worker and an independently verified end-to-end kill-switch audit are still required before any testnet automation should be treated as operationally ready. **Live trading remains disabled and unauthorized.**

## Bounded recovery sweep

`reconcile_unresolved_testnet_entry_reservations(symbol_by_client_order_id, limit=100)` provides a bounded, read-only sweep over the recovery inventory. The caller must supply an explicit mapping from each opaque client order ID to its symbol; the adapter does not guess symbols. `SUBMITTING` and `UNKNOWN` records with a valid mapping are passed to the per-order reconciliation primitive. Missing mappings and invalid states are reported without exchange writes. `RESERVED` records are deliberately deferred for crash-window review: the sweep cannot prove that no POST was attempted, so it never releases them. The returned summary is an inventory of outcomes, not proof of atomic exchange/account state or a replacement for a supervised recovery process.


## Account-cap exposure de-duplication

The entry preflight obtains the bounded durable unresolved-reservation inventory and excludes an exchange open entry order from the aggregate open-order exposure only when its exact `clientOrderId` matches an active ledger reservation. The ledger continues counting that reservation in the atomic cap calculation. Unmatched exchange orders remain in external exposure; the implementation never subtracts aggregate reservation totals from aggregate exchange exposure. If the reservation inventory is unavailable, malformed, or a required client ID is missing when matching is needed, entry preflight fails closed.

This identity-based accounting avoids double-counting an entirely unfilled matched order. A partially filled order may still be conservatively over-counted because the exchange position snapshot can include its filled portion while the ledger retains the full original reservation until terminal reconciliation. Separate exchange reads are not atomic, so this is still a conservative preflight rather than a race-free account snapshot. Do not weaken unresolved reservations or infer terminal status from a missing open-order row alone.
