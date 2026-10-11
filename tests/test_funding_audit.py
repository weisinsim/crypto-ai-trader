import pytest

from research.funding_audit import audit_funding


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


def test_approved_mark_price_sources_pass_provenance_gate():
    rows = funding([0, 8*3_600_000, 16*3_600_000], "mark_kline_open")
    rows[1]["mark_price_source"] = "mark_kline_open_nearest_1s"
    report = audit_funding(rows, candles(24), "BTCUSDT")
    assert report["status"] == "PASS"
    assert report["unverified_price_source_records"] == 0


def test_audit_rejects_funding_event_outside_candle_coverage():
    with pytest.raises(ValueError, match="outside candle coverage"):
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
