"""Exchange rates for prices in USD/EUR: how much one coin is worth. Docs: docs/api.md#prices-in-usd-or-eur

Two public sources, no API keys: Kraken (lists every coin we support) and Coinbase (all but TRX).
Where both list a coin they must agree within MAX_SPREAD, and their average is used; a coin only one
source lists uses that one. No fresh rate means the coin can't be chosen: never a stale price.
USDT counts as exactly 1 USD.
"""
import threading
import time
from decimal import ROUND_UP, Decimal

import requests

CURRENCIES = ("USD", "EUR")
COINS = ("BTC", "LTC", "DOGE", "ETH", "BNB", "TRX", "USDT")
TTL = 60                      # seconds a fetched rate is good for
MAX_SPREAD = Decimal("0.02")  # sources further apart than 2% = something's wrong: refuse
TIMEOUT = 10
HEADERS = {"User-Agent": "payment-gateway-rates/1.0"}
# Kraken's ticker keys per coin (its names are irregular: XXBTZUSD, XDGUSD, ...).
KRAKEN = {
    "USD": {"BTC": "XXBTZUSD", "ETH": "XETHZUSD", "LTC": "XLTCZUSD", "DOGE": "XDGUSD", "TRX": "TRXUSD",
            "BNB": "BNBUSD", "USDT": "USDTZUSD"},
    "EUR": {"BTC": "XXBTZEUR", "ETH": "XETHZEUR", "LTC": "XLTCZEUR", "DOGE": "XDGEUR", "TRX": "TRXEUR",
            "BNB": "BNBEUR", "USDT": "USDTEUR"},
}

_cache = {}  # currency -> (fetched_at monotonic, {coin: Decimal})
_lock = threading.Lock()


def kraken(currency):
    keys = KRAKEN[currency]
    data = requests.get("https://api.kraken.com/0/public/Ticker", params={"pair": ",".join(keys.values())},
                        headers=HEADERS, timeout=TIMEOUT).json()
    if data.get("error"):
        raise ValueError(f"kraken: {data['error']}")
    result = data["result"]
    return {coin: Decimal(result[key]["c"][0]) for coin, key in keys.items() if key in result}


def coinbase(currency):
    data = requests.get("https://api.coinbase.com/v2/exchange-rates", params={"currency": currency},
                        headers=HEADERS, timeout=TIMEOUT).json()
    rates = data["data"]["rates"]  # 1 USD = x coin
    return {coin: 1 / Decimal(rates[coin]) for coin in COINS if coin in rates and Decimal(rates[coin]) > 0}


SOURCES = (kraken, coinbase)


def combine(quotes):
    """quotes: [{coin: price}] from the sources that answered -> {coin: price} we trust."""
    out = {}
    for coin in COINS:
        prices = [q[coin] for q in quotes if coin in q and q[coin] > 0]
        if not prices:
            continue
        low, high = min(prices), max(prices)
        if (high - low) / low > MAX_SPREAD:
            continue  # the sources disagree: one of them is broken or the market is wild
        out[coin] = (sum(prices) / len(prices)).quantize(Decimal("1e-8"))  # 8 decimals: a readable, stated rate
    return out


def current(currency):
    """{coin: price of one coin in `currency`} (Decimal), fresh within TTL. Missing coin = unavailable."""
    with _lock:
        hit = _cache.get(currency)
        if hit and time.monotonic() - hit[0] < TTL:
            return hit[1]
        quotes = []
        for source in SOURCES:
            try:
                quotes.append(source(currency))
            except (requests.RequestException, ValueError, KeyError, TypeError, ArithmeticError):
                continue  # this source is down; the other may still answer
        prices = combine(quotes)
        if currency == "USD":
            prices["USDT"] = Decimal(1)
        if prices:
            _cache[currency] = (time.monotonic(), prices)  # failures aren't cached: retry next time
        return prices


def convert(price, currency, asset, decimals):
    """(amount string in the coin, rate) for `price` in `currency`, or None if no trusted rate.
    Rounded UP to the coin's smallest unit: the merchant never receives less than the price."""
    rate = current(currency).get(asset)  # test-network coins (Sepolia ETH, Nile TRX) are priced like the real ones
    if not rate:
        return None
    amount = (Decimal(price) / rate).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_UP)
    return format(amount.normalize(), "f"), format(rate.normalize(), "f")
