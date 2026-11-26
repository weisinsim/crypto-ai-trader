# Pionex ordinary perpetual: live read-only acceptance gate

Status: **NOT VERIFIED**. This document is a checklist, not evidence that the live API or alerts work.

## Hard boundaries

- PR #195 remains Draft. No merge, production deployment, or automatic orders.
- Never paste API keys, secrets, raw balances, or positions into chat, logs, issues, or PR comments.
- Production Render Environment currently holds credentials; this does **not** prove that the deployed `main` contains the draft reader.
- A free Render web service that sleeps is not evidence of a reliable continuous alert worker.
- Run live probes only after the user explicitly approves the isolated execution and its environment.
- The probe requires both `PIONEX_READ_ONLY_ENABLED=true` and `PIONEX_LIVE_PROBE_APPROVED=true` in that **isolated** environment. Do not change production flags merely to run a probe.
- Never grant API trade/robot trade permissions.

## Gate 1 — isolated connectivity

1. Identify a one-off isolated execution environment and its credential source, without exposing credentials.
2. Obtain explicit user approval for a GET-only request to Pionex.
3. Run `python -m notifications.pionex_connection_probe` from the reviewed PR commit in the isolated environment.
4. Record only: commit SHA, pass/fail, timestamp, sanitized response shape, and whether read-only permissions were verified.
5. A successful response only establishes basic GET connectivity; it does **not** validate positions schema, fills, pagination, SL/TP, or notifications.
6. Remove the temporary approval flag and verify that the probe refuses to run.

## Gate 2 — exchange semantics

- Confirm actual ordinary perpetual positions response fields and open/closed position semantics.
- Confirm `symbol` format and timestamp units from real response evidence, without recording sensitive values.
- Confirm fills pagination, inclusive/exclusive start/end boundaries, stable IDs, duplicates, and rate limits.
- Confirm history positions and orders do not cause false REAL_EXIT signals.
- Verify read-only API permission and that no POST/PUT/DELETE paths are present in the transport.

## Gate 3 — reconciliation and recovery

- Test checkpoint restart, backfill gaps, overlapping windows, and deduplication against live read-only data.
- Validate partial closes, full closes, manual closes, stop loss, take profit, and missing/late fills.
- Fail closed: no real-exit alert when data is ambiguous or stale.
- Confirm bounded retry, monitoring, and recovery after downtime.

## Gate 4 — delivery and operations

- Prove Dashboard displays fresh, timestamped account-backed alerts.
- Prove actual ChatGPT delivery to the intended recipient; a local print or CI test is insufficient.
- Prove worker reliability, alert delay, duplicate suppression, and failure monitoring under production-like conditions.
- Get separate explicit approval before any PR merge or production deployment.

## Evidence ledger

| Gate | Result | Evidence |
| --- | --- | --- |
| Offline CI | Recheck per commit | GitHub PR #195 workflow runs |
| Live connectivity | NOT VERIFIED | None |
| Exchange semantics | NOT VERIFIED | None |
| Recovery against real data | NOT VERIFIED | None |
| Dashboard delivery | NOT VERIFIED | None |
| ChatGPT delivery | NOT VERIFIED | None |

**Acceptance:** Do not announce live-ready until gates 1–4 have direct evidence and a separately approved deployment has been verified. Signals remain advisory; the user places all trades manually.
