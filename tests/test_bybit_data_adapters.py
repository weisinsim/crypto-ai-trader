from __future__ import annotations

import pytest

from research import bybit_candle_data, bybit_funding_data


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, params=None):
        self.urls.append((url, params))
        if not self.responses:
            raise AssertionError(f"unexpected HTTP request: {url}")
        return FakeResponse(self.responses.pop(0))


def test_bybit_candle_adapter_normalizes_and_validates(monkeypatch):
    fake = FakeClient([{
        "retCode": 0,
        "result": {"list": [
            ["1000", "10", "12", "9", "11", "3", "33"],
            ["2000", "11", "13", "10", "12", "4", "48"],
        ]},
    }])
    monkeypatch.setattr(bybit_candle_data.httpx, "Client", lambda timeout: fake)

    rows = bybit_candle_data.download_bybit_candles("BTCUSDT", "60", 0, 3000, pause_seconds=0)

    assert [row["t"] for row in rows] == [1000, 2000]
    assert rows[0]["venue"] == "bybit"
    assert rows[0]["o"] == 10
    assert rows[1]["v"] == 4
    assert "api.bytick.com" in fake.urls[0][0]


def test_bybit_candle_adapter_rejects_inconsistent_ohlc(monkeypatch):
    fake = FakeClient([{
        "retCode": 0,
        "result": {"list": [["1000", "10", "9", "8", "11", "3", "33"]]},
    }])
    monkeypatch.setattr(bybit_candle_data.httpx, "Client", lambda timeout: fake)

    with pytest.raises(ValueError, match="inconsistent Bybit OHLC"):
        bybit_candle_data.download_bybit_candles("BTCUSDT", "60", 0, 3000, pause_seconds=0)


def test_bybit_funding_requires_exact_official_mark_candle(monkeypatch):
    timestamp = 1_000_000
    fake = FakeClient([
        {"retCode": 0, "result": {"list": [
            {"fundingRateTimestamp": str(timestamp), "fundingRate": "0.0001"}
        ]}},
        {"retCode": 0, "result": {"list": [[str(timestamp), "50000", "50100", "49900", "50050"]]}},
    ])
    monkeypatch.setattr(bybit_funding_data.httpx, "Client", lambda timeout: fake)

    rows = bybit_funding_data.download_bybit_funding(
        "BTCUSDT", timestamp - 1000, timestamp + 1000, pause_seconds=0
    )

    assert len(rows) == 1
    assert rows[0]["funding_rate"] == pytest.approx(0.0001)
    assert rows[0]["mark_price"] == 50000
    assert rows[0]["mark_price_source"] == "bybit_mark_kline_open"
    assert all("api.bytick.com" in url for url, _ in fake.urls)


def test_bybit_funding_fails_closed_when_mark_candle_missing(monkeypatch):
    timestamp = 1_000_000
    fake = FakeClient([
        {"retCode": 0, "result": {"list": [
            {"fundingRateTimestamp": str(timestamp), "fundingRate": "0.0001"}
        ]}},
        {"retCode": 0, "result": {"list": [[str(timestamp + 60_000), "50000", "50100", "49900", "50050"]]}},
    ])
    monkeypatch.setattr(bybit_funding_data.httpx, "Client", lambda timeout: fake)

    with pytest.raises(ValueError, match="missing exact Bybit mark-price candle"):
        bybit_funding_data.download_bybit_funding(
            "BTCUSDT", timestamp - 1000, timestamp + 1000, pause_seconds=0
        )


@pytest.mark.parametrize("symbol", [None, 7, [], ""])
def test_bybit_funding_rejects_invalid_symbol_before_request(monkeypatch, symbol):
    def no_client(**kwargs):
        raise AssertionError("HTTP client must not be created for invalid input")
    monkeypatch.setattr(bybit_funding_data.httpx, "Client", no_client)
    with pytest.raises(ValueError, match="symbol"):
        bybit_funding_data.download_bybit_funding(symbol, 1, 2, pause_seconds=0)


@pytest.mark.parametrize(
    ("start_ms", "end_ms"),
    [(True, 2), (1, False), (1.5, 2), (1, 2.5), (-1, 2), (2, 2)],
)
def test_bybit_funding_rejects_invalid_timestamp_ranges(monkeypatch, start_ms, end_ms):
    def no_client(**kwargs):
        raise AssertionError("HTTP client must not be created for invalid input")
    monkeypatch.setattr(bybit_funding_data.httpx, "Client", no_client)
    with pytest.raises(ValueError, match="integer timestamps"):
        bybit_funding_data.download_bybit_funding("BTCUSDT", start_ms, end_ms, pause_seconds=0)


@pytest.mark.parametrize("pause_seconds", [True, -0.1, float("inf"), float("nan"), "0"])
def test_bybit_funding_rejects_invalid_pause(monkeypatch, pause_seconds):
    def no_client(**kwargs):
        raise AssertionError("HTTP client must not be created for invalid input")
    monkeypatch.setattr(bybit_funding_data.httpx, "Client", no_client)
    with pytest.raises(ValueError, match="pause_seconds"):
        bybit_funding_data.download_bybit_funding("BTCUSDT", 1, 2, pause_seconds=pause_seconds)


@pytest.mark.parametrize(
    "bad_row",
    [None, [], "bad", {}, {"fundingRateTimestamp": "1000"}, {"fundingRate": "0.1"}],
)
def test_bybit_funding_rejects_malformed_rows(monkeypatch, bad_row):
    fake = FakeClient([{"retCode": 0, "result": {"list": [bad_row]}}])
    monkeypatch.setattr(bybit_funding_data.httpx, "Client", lambda timeout: fake)
    with pytest.raises(ValueError, match="Bybit funding row"):
        bybit_funding_data.download_bybit_funding("BTCUSDT", 0, 2000, pause_seconds=0)


@pytest.mark.parametrize("bad_rate", [True, None, "not-a-number", "nan", "inf"])
def test_bybit_funding_rejects_invalid_rate_values(monkeypatch, bad_rate):
    fake = FakeClient([{"retCode": 0, "result": {"list": [
        {"fundingRateTimestamp": "1000", "fundingRate": bad_rate}
    ]}}])
    monkeypatch.setattr(bybit_funding_data.httpx, "Client", lambda timeout: fake)
    with pytest.raises(ValueError, match="funding rate"):
        bybit_funding_data.download_bybit_funding("BTCUSDT", 0, 2000, pause_seconds=0)
