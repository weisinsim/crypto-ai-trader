#!/usr/bin/env python3
"""Diagnose funding/candle timestamp alignment without modifying timestamps.

Accepts candle CSVs with a timestamp or t column and funding CSVs with
timestamp or funding_time_ms/fundingTime columns. Reports UTC ranges and nearest
candle-open deltas; never snaps or shifts funding events.
"""
import argparse
import bisect
import csv
import json
from datetime import datetime, timezone
from pathlib import Path


def _epoch_ms(value, *, numeric_is_ms=True):
    text = str(value).strip()
    if text.isdigit() or (text.startswith('-') and text[1:].isdigit()):
        if not numeric_is_ms:
            raise ValueError(f"numeric timestamp is not supported here: {value!r}")
        return int(text)
    dt = datetime.fromisoformat(text.replace('Z', '+00:00'))
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(f"timestamp must include timezone: {value!r}")
    return int(dt.timestamp() * 1000)


def _read_timestamps(path, candidates):
    with open(path, newline='', encoding='utf-8-sig') as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            raise ValueError(f"missing CSV header: {path}")
        field = next((name for name in candidates if name in reader.fieldnames), None)
        if field is None:
            raise ValueError(f"none of timestamp columns {candidates} found in {path}")
        values = []
        for line, row in enumerate(reader, start=2):
            raw = row.get(field)
            if raw in (None, ''):
                raise ValueError(f"empty {field} at line {line} in {path}")
            try:
                values.append(_epoch_ms(raw))
            except (ValueError, TypeError, OverflowError) as exc:
                raise ValueError(f"invalid timestamp at line {line} in {path}: {exc}") from exc
    if not values:
        raise ValueError(f"no timestamp rows in {path}")
    return values, field


def _iso(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat().replace('+00:00', 'Z')


def diagnose(candle_path, funding_path):
    candles, candle_field = _read_timestamps(candle_path, ('timestamp', 't', 'open_time_ms', 'openTime'))
    funding, funding_field = _read_timestamps(funding_path, ('funding_time_ms', 'fundingTime', 'timestamp'))
    candle_sorted = sorted(set(candles))
    duplicate_candles = len(candles) - len(set(candles))
    duplicate_funding = len(funding) - len(set(funding))
    deltas = []
    outside = 0
    for event in sorted(funding):
        candidates = candle_sorted[max(0, index - 1):min(len(candle_sorted), index + 1)]
        nearest = min(candidates, key=lambda candle: (abs(candle - event), candle))
        delta = event - nearest
        if event < candle_sorted[0] or event > candle_sorted[-1]:
            outside += 1
        deltas.append(delta)
    counts = {}
    for delta in deltas:
        counts[str(delta)] = counts.get(str(delta), 0) + 1
    return {
        'candle_file': str(candle_path), 'funding_file': str(funding_path),
        'candle_timestamp_field': candle_field, 'funding_timestamp_field': funding_field,
        'candle_rows': len(candles), 'funding_rows': len(funding),
        'duplicate_candle_timestamps': duplicate_candles, 'duplicate_funding_timestamps': duplicate_funding,
        'candle_first_utc': _iso(min(candles)), 'candle_last_utc': _iso(max(candles)),
        'funding_first_utc': _iso(min(funding)), 'funding_last_utc': _iso(max(funding)),
        'candle_first_mod_hour_ms': min(candles) % 3600000,
        'funding_first_mod_hour_ms': min(funding) % 3600000,
        'funding_events_outside_candle_timestamp_range': outside,
        'nearest_candle_delta_ms_counts': counts,
        'nearest_delta_examples_ms': deltas[:20],
        'notice': 'Diagnostic only: no timestamps were shifted; candle-open proximity does not prove settlement eligibility.'
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candles', required=True)
    parser.add_argument('--funding', required=True)
    args = parser.parse_args()
    print(json.dumps(diagnose(Path(args.candles), Path(args.funding)), indent=2))


if __name__ == '__main__':
    main()
