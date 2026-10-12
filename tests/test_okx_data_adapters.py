import pytest

from research import okx_candle_data, okx_funding_data


class Response:
    def __init__(self, payload):
        self.payload = payload
    def raise_for_status(self):
        return None
    def json(self):
        return self.payload


class Client:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def get(self, url, params=None):
        self.calls.append((url, params))
        if not self.payloads:
            raise AssertionError("unexpected request")
        return Response(self.payloads.pop(0))


def test_okx_candle_download_normalizes_completed_candles(monkeypatch):
    fake = Client([{"code":"0","data":[
        ["2000","11","13","10","12","4","4","48","1"],
        ["1000","10","12","9","11","3","3","33","1"],
    ]}])
    monkeypatch.setattr(okx_candle_data.httpx, "Client", lambda timeout: fake)
    rows = okx_candle_data.download_okx_candles("BTCUSDT", 1000, 3000, pause_seconds=0)
    assert [x["t"] for x in rows] == [1000, 2000]
    assert rows[0]["venue"] == "okx"
    assert rows[1]["q"] == 48


def test_okx_funding_requires_exact_mark_candle(monkeypatch):
    ts = 1_000_000
    fake = Client([
        {"code":"0","data":[{"fundingTime":str(ts),"realizedRate":"0.0001","fundingRate":"0.0002"}]},
        {"code":"0","data":[[str(ts),"50000","50100","49900","50050","1"]]},
        {"code":"0","data":[]},
    ])
    monkeypatch.setattr(okx_funding_data.httpx, "Client", lambda timeout: fake)
    rows = okx_funding_data.download_okx_funding("BTCUSDT", ts-1000, ts+1000, pause_seconds=0)
    assert len(rows) == 1
    assert rows[0]["funding_rate"] == pytest.approx(0.0001)
    assert rows[0]["mark_price"] == 50000
    assert rows[0]["mark_price_source"] == "okx_mark_price_kline_open"


def test_okx_funding_fails_closed_if_exact_mark_candle_missing(monkeypatch):
    ts = 1_000_000
    fake = Client([
        {"code":"0","data":[{"fundingTime":str(ts),"realizedRate":"0.0001","fundingRate":"0.0002"}]},
        {"code":"0","data":[[str(ts+60_000),"50000","50100","49900","50050","1"]]},
        {"code":"0","data":[]},
    ])
    monkeypatch.setattr(okx_funding_data.httpx, "Client", lambda timeout: fake)
    with pytest.raises(ValueError, match="missing exact OKX mark-price candle"):
        okx_funding_data.download_okx_funding("BTCUSDT", ts-1000, ts+1000, pause_seconds=0)
