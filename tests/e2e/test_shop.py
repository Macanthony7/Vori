"""Shop name and logo set in the wallet, shown on the checkout page; "Back to shop" after paying."""
import struct
import zlib

import httpx
from playwright.sync_api import expect

from conftest import import_wallet, tab, unlock

LONG_NAME = "The Extraordinarily Long Named Corner Coffee Roastery Shop"  # 58 chars: layout check


def tiny_png(width=40, height=20):
    """A real, decodable PNG (solid color), so the test can check the logo actually renders."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    rows = b"".join(b"\x00" + b"\x20\x60\xc0" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def test_shop_branding_and_return_link(page, browser, server, chain, tick, tmp_path):
    import_wallet(page)
    unlock(page)
    tab(page, "settings")
    logo = tmp_path / "logo.png"
    logo.write_bytes(tiny_png())
    page.fill("#shop-name", LONG_NAME)
    page.set_input_files("#shop-logo", str(logo))
    expect(page.locator("#shop-logo-box")).to_be_visible()
    page.click("#shop-save")
    expect(page.locator("#shop-result")).to_contain_text("with your logo")

    too_big = tmp_path / "big.png"
    too_big.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 120_000)
    page.set_input_files("#shop-logo", str(too_big))
    expect(page.locator("#shop-error")).to_contain_text("100 KB")

    # The shop's server creates the payment with a return link (API key from the wallet).
    page.click("#new-key")
    page.click("#new-key")
    page.wait_for_selector("#new-key-box:not([hidden])")
    key = {"Authorization": "Bearer " + page.input_value("#new-key-value")}
    p = httpx.post(f"{server}/payments", headers=key, json={
        "chain": "TRX", "asset": "TRX", "amount": "3", "return_url": "https://shop.example/thanks?order=7"}).json()

    for viewport in ({"width": 1280, "height": 900}, {"width": 375, "height": 812}):
        checkout = browser.new_context(base_url=server, viewport=viewport).new_page()
        try:
            checkout.goto(f"/pay/{p['id']}")
            expect(checkout.locator("#shop-name")).to_have_text(LONG_NAME)
            logo_img = checkout.locator("#shop-logo")
            expect(logo_img).to_be_visible()
            assert logo_img.evaluate("img => img.complete && img.naturalWidth") == 40, "the logo really renders"
            assert checkout.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), viewport
            expect(checkout.locator("#back")).to_be_hidden()  # nothing to go back to while paying
        finally:
            checkout.context.close()

    chain.pay("TRX", "TRX", p["address"], 3_000_000)
    tick()
    checkout = browser.new_context(base_url=server).new_page()
    try:
        checkout.goto(f"/pay/{p['id']}")
        expect(checkout.locator("#status")).to_contain_text("Paid")
        link = checkout.locator("#back-link")
        expect(link).to_have_text(f"Back to {LONG_NAME}")
        expect(link).to_have_attribute("href", "https://shop.example/thanks?order=7")
    finally:
        checkout.context.close()

    # Removing the logo and name takes them off the checkout page.
    page.locator("#shop-logo-remove").click()
    page.fill("#shop-name", "")
    page.click("#shop-save")
    expect(page.locator("#shop-result")).to_contain_text("no name")
    co = httpx.get(f"{server}/pay/{p['id']}/status").json()
    assert co["shop_name"] is None and co["logo_url"] is None
