"""A payment priced in USD: created in the wallet, the payer picks the coin on the checkout page, pays."""
from decimal import Decimal

import pytest
from playwright.sync_api import expect

import rates
from conftest import import_wallet, tab, unlock

USD = {"BTC": Decimal("60000"), "ETH": Decimal("2000"), "TRX": Decimal("0.30"), "USDT": Decimal(1)}  # no LTC/DOGE/BNB


@pytest.fixture
def fixed_rates(monkeypatch):
    monkeypatch.setattr(rates, "current", lambda currency: USD if currency == "USD" else {})


def test_payer_picks_the_coin_then_pays(page, browser, server, chain, tick, fixed_rates):
    import_wallet(page)
    unlock(page)
    page.select_option("#charge-unit", "USD")
    expect(page.locator("#charge-network-box")).to_be_hidden()
    page.fill("#charge-amount", "25")
    page.click("#charge-create")
    link = page.locator("#charge-result a")
    expect(page.locator("#charge-result")).to_contain_text("25.00 USD, the payer picks the coin")
    pid = link.get_attribute("href").split("/")[-1]
    tab(page, "payments")
    row = page.locator(f'#payments tbody tr:has(a[href="/pay/{pid}"])')  # this payment's row (others may exist)
    expect(row).to_contain_text("Choosing a coin")

    for viewport in ({"width": 375, "height": 812}, {"width": 1280, "height": 900}):
        checkout = browser.new_context(base_url=server, viewport=viewport).new_page()
        checkout.goto(f"/pay/{pid}")
        expect(checkout.locator("#choose")).to_be_visible()
        expect(checkout.locator("#price")).to_contain_text("25.00")
        options = checkout.locator("#options button")
        expect(checkout.locator("#options button", has_text="0.00041667 BTC")).to_be_enabled()
        expect(checkout.locator("#options button", has_text="25 USDT").first).to_be_enabled()
        expect(checkout.locator("#options button", has_text="LTC")).to_be_disabled()  # no trusted rate
        assert options.count() >= 7
        assert checkout.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), viewport
        if viewport["width"] == 375:
            checkout.context.close()

    checkout.locator("#options button", has_text="TRX on Tron").filter(has_text="83.333334 TRX").click()
    expect(checkout.locator("#payment")).to_be_visible()
    expect(checkout.locator("#choose")).to_be_hidden()
    expect(checkout.locator("#amount")).to_have_text("83.333334")
    expect(checkout.locator("#heading")).to_contain_text("$25.00")
    address = checkout.input_value("#address")
    assert address.startswith("T")

    chain.pay("TRX", "TRX", address, 83_333_334)
    tick()
    expect(checkout.locator("#status")).to_contain_text("Paid")
    checkout.context.close()

    page.reload()
    unlock(page)
    tab(page, "payments")
    row = page.locator("#payments tbody tr", has_text="83.333334 TRX")
    expect(row).to_contain_text("= 25.00 USD")
    expect(row).to_contain_text("Confirmed")
