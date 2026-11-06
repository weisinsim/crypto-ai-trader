"""Offline-only, fail-closed pagination validator for Pionex fill normalization.

A caller must first validate the exchange-specific response schema and translate
each API page to {fills: [...], next_cursor: str | None}. This module never
makes API requests or treats a truncated page as complete evidence.
"""
from __future__ import annotations


class PaginationError(ValueError):
    pass


def collect_verified_fill_pages(pages, *, expected_symbol, complete=False,
                                max_pages=100, max_fills=10000):
    """Combine already fetched pages, rejecting gaps, duplicates and ambiguity.

    Each page must contain exactly fills and next_cursor; the first page has no
    input cursor and each later page must supply the preceding next_cursor in
    its request_cursor. All pages must be available before calling this method.
    """
    if complete is not True:
        raise PaginationError("Caller has not verified the terminal page")
    if (not isinstance(expected_symbol, str)
            or not expected_symbol.endswith("_USDT_PERP")):
        raise PaginationError("Unsupported futures symbol")
    if not isinstance(pages, list) or not pages or len(pages) > max_pages:
        raise PaginationError("Missing or excessive pages")
    if type(max_pages) is not int or type(max_fills) is not int or max_pages <= 0 or max_fills <= 0:
        raise PaginationError("Invalid safety bounds")
    previous_cursor = None
    all_fills = []
    seen_ids = set()
    seen_cursors = set()
    for index, page in enumerate(pages):
        if not isinstance(page, dict) or set(page) != {"request_cursor", "next_cursor", "fills"}:
            raise PaginationError("Invalid normalized page")
        if page["request_cursor"] != previous_cursor:
            raise PaginationError("Cursor chain gap or wrong page order")
        if not isinstance(page["fills"], list):
            raise PaginationError("Missing fill list")
        next_cursor = page["next_cursor"]
        if next_cursor is not None and (not isinstance(next_cursor, str) or not next_cursor):
            raise PaginationError("Invalid next cursor")
        if next_cursor is None and index != len(pages) - 1:
            raise PaginationError("Unexpected terminal page")
        if next_cursor is not None and index == len(pages) - 1:
            raise PaginationError("Unfetched next page")
        if next_cursor is not None:
            if next_cursor in seen_cursors:
                raise PaginationError("Repeated cursor")
            seen_cursors.add(next_cursor)
        for fill in page["fills"]:
            if not isinstance(fill, dict) or fill.get("symbol") != expected_symbol:
                raise PaginationError("Fill symbol mismatch")
            fill_id = fill.get("fill_id")
            if (not isinstance(fill_id, (str, int)) or isinstance(fill_id, bool)
                    or not str(fill_id) or str(fill_id) in seen_ids):
                raise PaginationError("Missing or duplicate exchange fill ID")
            seen_ids.add(str(fill_id))
            all_fills.append(fill)
            if len(all_fills) > max_fills:
                raise PaginationError("Too many fills to verify")
        previous_cursor = next_cursor
    return all_fills
