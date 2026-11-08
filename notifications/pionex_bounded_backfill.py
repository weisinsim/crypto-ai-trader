"""Offline-testable, GET-only bounded fill-window collector.

The exchange's actual time-filter semantics still require live read-only
verification before this may be used as authoritative exit evidence.
No checkpoints, notifications, or trading operations are performed here.
"""
from notifications.pionex_official_fills import validate_fills_window, PionexFillSchemaError


class FillBackfillError(ValueError):
    pass


async def fetch_bounded_fills(reader, symbol, start_ms, end_ms, *,
                              limit=200, max_requests=128):
    """Fetch and normalize a bounded interval, splitting saturated windows.

    Assumes inclusive millisecond bounds; this assumption MUST be verified
    against real Pionex data before production use. A saturated one-ms window
    or exhausted request budget fails closed.
    """
    if (not isinstance(symbol, str) or not symbol.endswith("_USDT_PERP")
            or type(start_ms) is not int or type(end_ms) is not int
            or start_ms <= 0 or end_ms < start_ms
            or type(limit) is not int or not 1 <= limit <= 200
            or type(max_requests) is not int or max_requests <= 0):
        raise FillBackfillError("Invalid backfill bounds")
    pending = [(start_ms, end_ms)]
    requests = 0
    seen = {}
    while pending:
        lo, hi = pending.pop()
        if requests >= max_requests:
            raise FillBackfillError("Backfill request budget exhausted")
        requests += 1
        data = await reader.fills(symbol, startTime=lo, endTime=hi, limit=limit)
        if not isinstance(data, dict) or not isinstance(data.get("fills"), list):
            raise FillBackfillError("Unexpected exchange data shape")
        if len(data["fills"]) >= limit:
            if lo == hi:
                raise FillBackfillError("Single millisecond saturated; cannot prove completeness")
            mid = lo + (hi - lo) // 2
            pending.extend(((mid + 1, hi), (lo, mid)))
            continue
        try:
            fills = validate_fills_window(
                {"result": True, "data": data},
                symbol=symbol, start_ms=lo, end_ms=hi, limit=limit)
        except PionexFillSchemaError as exc:
            raise FillBackfillError("Unverified fill window") from exc
        for fill in fills:
            fid = fill["fill_id"]
            if fid in seen:
                raise FillBackfillError("Repeated fill identity across disjoint windows")
            seen[fid] = fill
    return sorted(seen.values(), key=lambda x: (x["timestamp_ms"], x["fill_id"]))
