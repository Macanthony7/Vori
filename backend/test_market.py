"""GET /market and /market/{coin}/history: the wallet's prices, 24 h change and chart (display only)."""
from decimal import Decimal

from fastapi.testclient import TestClient

import app as app_module
import rates

api = TestClient(app_module.app)


def test_market_endpoints(monkeypatch):
    monkeypatch.setattr(rates, "market", lambda c: {"BTC": {"price": Decimal("60000.10"), "change_24h": Decimal("-0.0125")},
                                                     "TRX": {"price": None, "change_24h": None}})
    monkeypatch.setattr(rates, "history", lambda coin, span, c: [(1, Decimal("1.50")), (2, Decimal("1.6"))])
    r = api.get("/market")
    assert r.status_code == 200
    assert r.json()["coins"] == {"BTC": {"price": "60000.1", "change_24h": "-0.0125"}, "TRX": {"price": None, "change_24h": None}}
    assert api.get("/market?currency=GBP").status_code == 400
    h = api.get("/market/btc/history?span=1w")
    assert h.json() == {"coin": "BTC", "span": "1W", "currency": "USD", "points": [[1, "1.5"], [2, "1.6"]]}
    assert api.get("/market/XYZ/history").status_code == 404
    assert api.get("/market/BTC/history?span=5Y").status_code == 404


def test_live_feed_parses_kraken_ticker(monkeypatch):
    import live
    monkeypatch.setattr(live, "prices", {})
    live.handle('{"channel":"status","type":"update","data":[{"system":"online"}]}')
    live.handle('{"channel":"ticker","type":"snapshot","data":[{"symbol":"BTC/USD","last":59806.4,"change_pct":-1.65},'
                '{"symbol":"XYZ/USD","last":1.0,"change_pct":0},{"symbol":"ETH/USD","last":0,"change_pct":1}]}')
    live.handle('{"channel":"ticker","type":"update","data":[{"symbol":"DOGE/USD","last":0.0576274}]}')
    assert live.prices == {"BTC": (Decimal("59806.4"), Decimal("-0.0165")), "DOGE": (Decimal("0.0576274"), None)}, \
        "exact decimals from the text; unknown coins and zero prices ignored"
    assert live.snapshot() == live.prices
    monkeypatch.setattr(live, "last_message", live.last_message - live.SILENT - 1)
    assert live.snapshot() == {}, "a silent feed counts as down"


def test_market_stream_sends_live_prices(monkeypatch):
    import live
    monkeypatch.setattr(live, "start", lambda: None)
    monkeypatch.setattr(live, "snapshot", lambda: {"BTC": (Decimal("60001.5"), Decimal("0.01"))})
    monkeypatch.setattr(app_module, "STREAM_SECONDS", 1.5)
    assert api.get("/market/stream").status_code == 401, "wallet session needed"
    monkeypatch.setattr(app_module, "account_for", lambda authorization: {"id": "acct-stream"})
    with api.stream("GET", "/market/stream", headers={"Authorization": "Bearer gws_x"}) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        body = "".join(r.iter_text())
    assert body.count("data: ") == 1, "sent once, then only when a price moves"
    assert '"BTC": {"price": "60001.5", "change_24h": "0.01"}' in body
    assert app_module._streams["acct-stream"] == 0, "the open-stream count goes back down"
