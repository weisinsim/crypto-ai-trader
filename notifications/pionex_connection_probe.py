"""Opt-in, read-only Pionex connection probe.

Run in an isolated environment with the read-only credentials already configured.
Never logs credentials, raw positions, balances, or exchange response bodies.
This is a connectivity/schema smoke test, NOT evidence of completed reconciliation.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

from notifications.pionex_readonly import PionexFuturesReader, PionexReadError


async def probe_positions(reader: PionexFuturesReader) -> dict[str, object]:
    """Return only non-sensitive connectivity metadata; never raw account data."""
    try:
        data = await reader.positions()
    except Exception:
        return {"ok": False, "stage": "positions", "reason": "read_or_validation_failed"}
    if not isinstance(data, (dict, list)):
        return {"ok": False, "stage": "positions", "reason": "unexpected_data_shape"}
    return {
        "ok": True,
        "stage": "positions",
        "response_shape": "object" if isinstance(data, dict) else "array",
        "schema_verified": False,
        "fills_verified": False,
        "notifications_verified": False,
    }


def live_probe_authorized(environ=None) -> bool:
    """Require two independent explicit opt-ins before any live request."""
    env = os.environ if environ is None else environ
    return (env.get("PIONEX_READ_ONLY_ENABLED") == "true"
            and env.get("PIONEX_LIVE_PROBE_APPROVED") == "true")


async def main() -> int:
    if not live_probe_authorized():
        print(json.dumps({"ok": False, "stage": "configuration", "reason": "live_probe_not_approved"}))
        return 2
    try:
        reader = PionexFuturesReader.from_env()
    except (PionexReadError, ValueError):
        print(json.dumps({"ok": False, "stage": "configuration", "reason": "disabled_or_missing_credentials"}))
        return 2
    result = await probe_positions(reader)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
