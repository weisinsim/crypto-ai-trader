import hashlib

import pytest

from research.funding_audit import audit_funding, input_file_lineage


def candles(n=10):
    return [{"t": i * 3_600_000, "o": 100, "h": 101, "l": 99, "c": 100, "v": 1, "q": 100}
            for i in range(n)]


def funding(times, source="mark_kline_open"):
    return [{"funding_time": t, "funding_rate": 0.0001, "mark_price": 100,
             "symbol": "BTCUSDT", "mark_price_source": source} for t in times]


def test_audit_passes_sorted_unique_in_range_events():
    report = audit_funding(funding([0, 8*3_600_000, 16*3_600_000]),
                           candles(24), "BTCUSDT")
    assert report["status"] == "PASS"
    assert report["funding_records"] == 3
    assert report["max_gap_hours"] == 8


def test_audit_flags_large_gap():
    report = audit_funding(funding([0, 48*3_600_000]), candles(72), "BTCUSDT")
    assert report["status"] == "FAIL_GAP"
    assert report["gaps_over_24h"] == 1


def test_audit_rejects_wrong_symbol():
    with pytest.raises(ValueError, match="symbol mismatch"):
        audit_funding(funding([0]), candles(), "ETHUSDT")


def test_audit_rejects_duplicate_timestamps():
    with pytest.raises(ValueError, match="duplicate"):
        audit_funding(funding([0, 0]), candles(), "BTCUSDT")


def test_audit_reports_proxy_prices():
    report = audit_funding(funding([0], "prior_trade_kline_close_proxy"),
                           candles(), "BTCUSDT")
    assert report["proxy_price_records"] == 1
    assert report["status"] == "FAIL_PROXY_MARK_PRICE"


def test_mixed_sources_fail_clean_audit_when_any_proxy_is_present():
    rows = funding([0, 8*3_600_000, 16*3_600_000])
    rows[-1]["mark_price_source"] = "prior_trade_kline_close_proxy"
    report = audit_funding(rows, candles(24), "BTCUSDT")
    assert report["status"] == "FAIL_PROXY_MARK_PRICE"
    assert report["proxy_price_records"] == 1


def test_unknown_mark_price_source_fails_audit():
    report = audit_funding(funding([0], "unspecified"), candles(), "BTCUSDT")
    assert report["status"] == "FAIL_UNVERIFIED_MARK_PRICE_SOURCE"
    assert report["unverified_price_source_records"] == 1


def test_nearest_timestamp_source_requires_review_even_when_within_one_second():
    rows = funding([0, 8*3_600_000, 16*3_600_000], "mark_kline_open")
    rows[1]["mark_price_source"] = "mark_kline_open_nearest_1s"
    report = audit_funding(rows, candles(24), "BTCUSDT")
    assert report["status"] == "FAIL_PROXY_MARK_PRICE"
    assert report["proxy_price_records"] == 1


def test_exact_time_one_minute_mark_candle_source_passes():
    rows = funding([0, 8*3_600_000, 16*3_600_000], "mark_kline_1m_open")
    report = audit_funding(rows, candles(24), "BTCUSDT")
    assert report["status"] == "PASS"
    assert report["unverified_price_source_records"] == 0

def test_nearest_one_minute_mark_candle_source_still_fails():
    rows = funding([0, 8*3_600_000], "mark_kline_1m_open_nearest_1s")
    report = audit_funding(rows, candles(24), "BTCUSDT")
    assert report["status"] == "FAIL_PROXY_MARK_PRICE"
    assert report["proxy_price_records"] == 2

def test_exact_known_source_labels_pass_provenance_gate():
    rows = funding([0, 8*3_600_000, 16*3_600_000], "mark_kline_open")
    report = audit_funding(rows, candles(24), "BTCUSDT")
    assert report["status"] == "PASS"
    assert report["unverified_price_source_records"] == 0


def test_exact_okx_mark_price_source_is_recognized_for_okx_confirmation():
    rows = funding([0, 8*3_600_000, 16*3_600_000], "okx_mark_price_kline_open")
    for row in rows:
        row["venue"] = "okx"
    report = audit_funding(rows, candles(24), "BTCUSDT")
    assert report["status"] == "PASS"
    assert report["unverified_price_source_records"] == 0


def test_okx_source_with_conflicting_venue_fails_closed():
    rows = funding([0], "okx_mark_price_kline_open")
    rows[0]["venue"] = "binance"
    report = audit_funding(rows, candles(), "BTCUSDT")
    assert report["status"] == "FAIL_UNVERIFIED_MARK_PRICE_SOURCE"
    assert report["unverified_price_source_records"] == 1


def test_okx_source_without_explicit_venue_fails_closed():
    rows = funding([0], "okx_mark_price_kline_open")
    report = audit_funding(rows, candles(), "BTCUSDT")
    assert report["status"] == "FAIL_UNVERIFIED_MARK_PRICE_SOURCE"
    assert report["unverified_price_source_records"] == 1


def test_audit_rejects_funding_event_outside_candle_coverage():
    with pytest.raises(ValueError, match="outside candle coverage"):
        audit_funding(funding([24 * 3_600_000]), candles(), "BTCUSDT")



def test_audit_rejects_negative_funding_timestamp_explicitly():
    with pytest.raises(ValueError, match="negative funding timestamp"):
        audit_funding(funding([-1]), candles(), "BTCUSDT")


def test_audit_rejects_non_finite_funding_rate():
    rows = funding([0])
    rows[0]["funding_rate"] = float("nan")
    with pytest.raises(ValueError, match="invalid funding record"):
        audit_funding(rows, candles(), "BTCUSDT")


def test_audit_rejects_non_positive_mark_price():
    rows = funding([0])
    rows[0]["mark_price"] = 0
    with pytest.raises(ValueError, match="invalid funding record"):
        audit_funding(rows, candles(), "BTCUSDT")


def test_audit_rejects_unsorted_funding_events():
    with pytest.raises(ValueError, match="not sorted"):
        audit_funding(funding([8*3_600_000, 0]), candles(24), "BTCUSDT")


def test_audit_rejects_unsorted_candles():
    rows = candles()
    rows[0], rows[1] = rows[1], rows[0]
    with pytest.raises(ValueError, match="candle timestamps"):
        audit_funding(funding([0]), rows, "BTCUSDT")


def test_audit_reports_16_hour_gap_without_claiming_complete_coverage():
    report = audit_funding(funding([0, 16*3_600_000]), candles(24), "BTCUSDT")
    assert report["status"] == "PASS"
    assert report["gaps_over_8h"] == 1
    assert report["max_gap_hours"] == 16
    assert any("do not prove" in item for item in report["limitations"])


def test_ambiguous_legacy_rest_mark_price_source_fails_provenance_gate():
    report = audit_funding(funding([0], "rest_mark_price"), candles(), "BTCUSDT")
    assert report["status"] == "FAIL_UNVERIFIED_MARK_PRICE_SOURCE"
    assert report["unverified_price_source_records"] == 1


def test_custom_eight_hour_gate_fails_sixteen_hour_gap():
    report = audit_funding(funding([0, 16*3_600_000]), candles(24), "BTCUSDT",
                           max_gap_hours=8.0)
    assert report["status"] == "FAIL_GAP"
    assert report["max_gap_gate_hours"] == 8.0


@pytest.mark.parametrize("threshold", [0, -1, float("nan"), float("inf"), float("-inf")])
def test_audit_rejects_invalid_gap_threshold(threshold):
    with pytest.raises(ValueError, match="max_gap_hours must be finite and greater than zero"):
        audit_funding(funding([0]), candles(), "BTCUSDT", max_gap_hours=threshold)


def test_audit_fails_when_funding_history_starts_late_inside_requested_window():
    rows = candles(24 * 180)
    first_event = 24 * 90 * 3_600_000
    last_event = first_event + 8 * 3_600_000
    report = audit_funding(funding([first_event, last_event]), rows, "BTCUSDT",
                           max_gap_hours=8.0)
    assert report["status"] == "FAIL_GAP"
    assert report["leading_gap_hours"] > 8
    assert report["boundary_gaps_over_8h"] >= 1


def test_binance_source_with_explicit_binance_venue_passes():
    rows = funding([0], "binance_fapi_funding_rate_mark_price")
    rows[0]["venue"] = "binance"
    report = audit_funding(rows, candles(), "BTCUSDT")
    assert report["status"] == "PASS"


def test_binance_venue_is_trimmed_and_case_normalized():
    rows = funding([0], "binance_fapi_funding_rate_mark_price")
    rows[0]["venue"] = " Binance "
    report = audit_funding(rows, candles(), "BTCUSDT")
    assert report["status"] == "PASS"


def test_expected_venue_rejects_valid_but_cross_venue_funding():
    rows = funding([0], "binance_fapi_funding_rate_mark_price")
    rows[0]["venue"] = "binance"
    report = audit_funding(rows, candles(), "BTCUSDT", expected_venue="okx")
    assert report["status"] == "FAIL_UNVERIFIED_MARK_PRICE_SOURCE"
    assert report["unverified_price_source_records"] == 1
    assert report["expected_venue"] == "okx"


def test_expected_venue_accepts_matching_venue():
    rows = funding([0], "okx_mark_price_kline_open")
    rows[0]["venue"] = "okx"
    report = audit_funding(rows, candles(), "BTCUSDT", expected_venue="OKX")
    assert report["status"] == "PASS"


def test_binance_source_without_venue_fails_closed():
    rows = funding([0], "binance_fapi_funding_rate_mark_price")
    report = audit_funding(rows, candles(), "BTCUSDT")
    assert report["status"] == "FAIL_UNVERIFIED_MARK_PRICE_SOURCE"
    assert report["unverified_price_source_records"] == 1


def test_binance_source_with_conflicting_venue_fails_closed():
    rows = funding([0], "binance_fapi_funding_rate_mark_price")
    rows[0]["venue"] = "okx"
    report = audit_funding(rows, candles(), "BTCUSDT")
    assert report["status"] == "FAIL_UNVERIFIED_MARK_PRICE_SOURCE"
    assert report["unverified_price_source_records"] == 1



def test_input_file_lineage_hashes_exact_file_bytes(tmp_path):
    path = tmp_path / "funding.csv"
    payload = b"funding_time,funding_rate\n123,0.001\n"
    path.write_bytes(payload)

    lineage = input_file_lineage(path)

    assert lineage["path"] == str(path)
    assert lineage["sha256"] == hashlib.sha256(payload).hexdigest()
    assert lineage["size_bytes"] == len(payload)


def test_input_file_lineage_changes_when_bytes_change(tmp_path):
    path = tmp_path / "candles.csv"
    path.write_bytes(b"t,c\n1,100\n")
    first = input_file_lineage(path)
    path.write_bytes(b"t,c\n1,101\n")
    second = input_file_lineage(path)

    assert first["sha256"] != second["sha256"]
    assert first["size_bytes"] != second["size_bytes"]
