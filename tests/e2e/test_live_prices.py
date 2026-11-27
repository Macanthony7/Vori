"""Live prices: a price that moves on the server's feed shows up in the open wallet within seconds."""
import re
from decimal import Decimal

from playwright.sync_api import expect

from conftest import import_wallet, open_coin, unlock


def test_prices_move_live_in_the_wallet(page, fake_market):
    import_wallet(page)
    unlock(page)
    btc = page.locator("#balances li", has=page.locator(".network", has_text=re.compile("^Bitcoin$")))
    expect(btc).to_contain_text("$60,000.00")  # from GET /market; the live feed is down so far

    fake_market["BTC"] = (Decimal("61234.5"), Decimal("0.02"))
    expect(btc).to_contain_text("$61,234.50", timeout=10_000)
    expect(btc).to_contain_text("+2.00%")

    open_coin(page, "Bitcoin")  # the coin screen follows too
    fake_market["BTC"] = (Decimal("59000"), Decimal("-0.01"))
    expect(page.locator("#chart-price")).to_have_text("$59,000.00", timeout=10_000)
    expect(page.locator("#coin-price")).to_have_text("$59,000.00 (-1.00%)")
