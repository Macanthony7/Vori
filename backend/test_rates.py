"""rates.py against recorded Kraken and Coinbase answers (shapes checked live on 2026-11-26), no network."""
from decimal import Decimal

import pytest
import requests

import rates

KRAKEN_USD = {"error": [], "result": {
    "XXBTZUSD": {"c": ["60684.90000", "0.01"]}, "XETHZUSD": {"c": ["1696.22000", "1"]},
    "XLTCZUSD": {"c": ["43.58000", "1"]}, "XDGUSD": {"c": ["0.056705900", "1"]}, "TRXUSD": {"c": ["0.3030640", "1"]},
    "BNBUSD": {"c": ["551.57000", "1"]}, "USDTZUSD": {"c": ["0.99874000", "1"]}}}
COINBASE_USD = {"data": {"currency": "USD", "rates": {
    "BTC": "0.0000164673", "ETH": "0.000589171", "LTC": "0.0229405", "DOGE": "17.6180", "BNB": "0.00181199",
    "USDT": "1.00126", "EUR": "0.9098"}}}  # 1 USD = x coin; no TRX, like the real answer


@pytest.fixture
def net(monkeypatch):
    answers = {}

    def get(url, params=None, **kw):
        reply = answers.get("kraken" if "kraken" in url else "coinbase")
        if isinstance(reply, Exception):
            raise reply

        class R:
            def json(self):
                return reply
        return R()
    monkeypatch.setattr(rates.requests, "get", get)
    rates._cache.clear()
    yield answers
    rates._cache.clear()


def test_both_sources_agree_average_and_single_source_coins(net):
    net.update(kraken=KRAKEN_USD, coinbase=COINBASE_USD)
    usd = rates.current("USD")
    assert Decimal("60684") < usd["BTC"] < Decimal("60727"), "average of both"
    assert usd["TRX"] == Decimal("0.30306400"), "only Kraken lists TRX: used as is"
    assert usd["USDT"] == 1, "USDT is exactly 1 USD"
    assert usd["BTC"] == usd["BTC"].quantize(Decimal("1e-8")), "rates are stated to 8 decimals"


def test_disagreeing_sources_make_a_coin_unavailable(net):
    broken = {"data": {"rates": {**COINBASE_USD["data"]["rates"], "ETH": "0.0005"}}}  # ETH 2000 vs 1696: 18% apart
    net.update(kraken=KRAKEN_USD, coinbase=broken)
    usd = rates.current("USD")
    assert "ETH" not in usd and "BTC" in usd


def test_one_source_down_still_prices_and_failures_are_not_cached(net):
    net.update(kraken=requests.ConnectionError("down"), coinbase=COINBASE_USD)
    usd = rates.current("USD")
    assert "BTC" in usd and "TRX" not in usd, "TRX needs Kraken"
    net.update(kraken={"error": ["EGeneral:Too many requests"]}, coinbase=ValueError("bad json"))
    rates._cache.clear()
    assert rates.current("EUR") == {}, "nothing trusted: every coin unavailable"
    net.update(kraken=KRAKEN_USD, coinbase=COINBASE_USD)
    assert "BTC" in rates.current("EUR"), "the failure wasn't cached: asked again and answered"


def test_cache_lasts_ttl(net, monkeypatch):
    net.update(kraken=KRAKEN_USD, coinbase=COINBASE_USD)
    clock = [1000.0]
    monkeypatch.setattr(rates.time, "monotonic", lambda: clock[0])
    first = rates.current("USD")
    net.update(kraken=requests.ConnectionError("down"), coinbase=requests.ConnectionError("down"))
    clock[0] += rates.TTL - 1
    assert rates.current("USD") is first, "cached"
    clock[0] += 2
    assert rates.current("USD") == {"USDT": 1}, "expired: asked again, never a stale price"


def test_convert_rounds_up_never_undercharges(net, monkeypatch):
    monkeypatch.setattr(rates, "current", lambda c: {"BTC": Decimal("60707.41"), "TRX": Decimal("0.303091"), "USDT": Decimal(1)})
    amount, rate = rates.convert("25.00", "USD", "BTC", 8)
    assert (amount, rate) == ("0.00041182", "60707.41")
    assert Decimal(amount) * Decimal(rate) >= 25, "rounded up to a whole satoshi"
    assert rates.convert("10", "USD", "TRX", 6)[0] == "32.993392"
    assert rates.convert("10", "USD", "USDT", 6) == ("10", "1")
    assert rates.convert("10", "USD", "ETH", 18) is None, "no rate: unavailable"
    for price in ("0.01", "1", "99.99", "12345.67"):
        a, r = rates.convert(price, "USD", "BTC", 8)
        assert Decimal(a) * Decimal(r) >= Decimal(price) and (Decimal(a) - Decimal("1e-8")) * Decimal(r) < Decimal(price)


def test_market_history_and_24h_change(monkeypatch):
    """Chart candles (shape recorded from Kraken's OHLC on 2026-11-27) and the 24 h change from the first one."""
    calls = []

    def get(url, params=None, **kw):
        calls.append(params)

        class R:
            def json(self):
                if params["pair"] == "XDGUSD":
                    return {"error": ["EQuery:Unknown asset pair"]}
                return {"error": [], "result": {params["pair"]: [
                    [1733529600 + i * 900, "1", "1", "1", str(100 + i), "1", "1", 1] for i in range(120)], "last": 1}}
        return R()
    monkeypatch.setattr(rates.requests, "get", get)
    monkeypatch.setattr(rates, "current", lambda c: {"BTC": Decimal("219.5"), "USDT": Decimal(1)})
    rates._history.clear()
    points = rates.history("BTC", "1D")
    assert len(points) == 96 and points[0] == (1733529600 + 24 * 900, Decimal(124)), "the last 96 quarter hours"
    assert rates.history("BTC", "1D") is points and len(calls) == 1, "cached"
    m = rates.market("USD")
    assert m["BTC"] == {"price": Decimal("219.5"), "change_24h": Decimal("0.7702")}, "(219.5 - 124) / 124"
    assert m["DOGE"] == {"price": None, "change_24h": None}, "no rate, no candles: unknown, not zero"
    assert m["ETH"]["price"] is None and m["ETH"]["change_24h"] is None
    assert rates.history("DOGE", "1D") == [] and ("DOGE", "1D", "USD") not in rates._history, "failures aren't cached"
    rates._history.clear()
