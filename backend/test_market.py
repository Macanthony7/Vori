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
