"""Wallet dashboard and the payer's checkout page, end to end: unlock, New payment, pay (fake chain),
watcher, statuses, explorer links, webhooks, late coins, API key, lock."""
import json

import httpx
from playwright.sync_api import expect

from conftest import PASSWORD, frame, import_wallet, new_payment, open_coin, screen, tab, unlock


def open_wallet(page):
    import_wallet(page)  # first run creates the account; later runs restore it (same phrase)
    unlock(page)


def status_text(checkout):
    return checkout.locator("#status").inner_text()


def test_unlock_balances_receive_and_lock(page, chain):
    open_wallet(page)
    balances = page.locator("#balances li")
    assert balances.count() >= 5, "one row per coin and network"
    open_coin(page, "Bitcoin")
    page.click("#coin-receive")
    address = page.input_value("#receive-address")
    assert address == "1LqBGSKuX5yYUonjxT5qGfpUsXKYYWeabA", "Receive shows the main address (index 0)"
    assert page.locator("#receive-qr canvas").count() == 1

    page.click("#lock")
    f = frame(page)
    # Lock must really reload the frame (keys gone from memory), not just hide the dashboard.
    expect(f.locator("#unlock")).to_be_visible()
    expect(f.locator("#unlocked")).to_be_hidden()
    assert page.is_hidden("#dashboard")
    f.locator("#u-pass").fill("wrong password")
    f.locator("#u-done").click()
    expect(f.locator("#u-error")).not_to_be_empty()
    f.locator("#u-pass").fill(PASSWORD)
    f.locator("#u-done").click()
    page.wait_for_selector("#content:not([hidden])", timeout=90_000)


def test_payment_from_checkout_to_paid(page, browser, server, chain, tick):
    open_wallet(page)
    pid = new_payment(page, "0.001", "BTC/BTC")

    checkout = browser.new_context(base_url=server).new_page()
    try:
        checkout.goto(f"/pay/{pid}")
        checkout.wait_for_selector("#payment:not([hidden])")
        assert checkout.locator("#amount").inner_text() == "0.001"
        address = checkout.input_value("#address")
        assert address.startswith("1") and checkout.locator("#qr canvas").count() == 1
        assert ":" in checkout.locator("#countdown").inner_text()

        chain.pay("BTC", "BTC", address, 40_000, confirmed=False, tx="aa" * 32)  # part of 100,000 sat
        tick()
        expect(checkout.locator("#status")).to_contain_text("Received 0.0004")
        assert "0.0006" in status_text(checkout), "asks for the rest"

        chain.pay("BTC", "BTC", address, 60_000, confirmed=False, tx="bb" * 32)
        tick()
        expect(checkout.locator("#status")).to_contain_text("Payment received")
        assert not checkout.is_visible("#pay-box"), "address hidden once paid"

        chain.confirm("BTC", "BTC", address)
        tick()
        expect(checkout.locator("#status")).to_contain_text("Paid")
        link = checkout.locator("#tx-link a")
        assert link.get_attribute("href").endswith("/bitcoin/address/" + address), "explorer: the address"
        assert "aa" * 32 not in checkout.content(), "no transaction hashes on the payer's page"
    finally:
        checkout.context.close()

    page.reload()
    unlock(page)
    tab(page, "payments")
    row = page.locator(f'#payments li[data-id="{pid}"]')
    assert "Confirmed" in row.inner_text()
    row.click()
    assert page.get_attribute("#pd-explorer", "href").endswith("/bitcoin/address/" + address), \
        "two transactions: the address page lists both"
    assert page.get_attribute("#pd-checkout", "href") == f"/pay/{pid}"


def test_expired_payment_then_late_coins(page, chain, tick):
    open_wallet(page)
    pid = new_payment(page, "5", "TRX/TRX")
    import db
    from contextlib import closing
    with closing(db.connect()) as conn, conn:
        conn.execute("UPDATE payments SET expires_at = '2000-01-01T00:00:00Z' WHERE id = ?", (pid,))
        address = conn.execute("SELECT address FROM payments WHERE id = ?", (pid,)).fetchone()["address"]
    tick()  # nothing arrived: expired
    chain.pay("TRX", "TRX", address, 5_000_000, tx="cc" * 32)
    tab(page, "wallet")
    page.click("#scan")  # far past the 24 h watch: only the scan finds it
    expect(page.locator("#scan-result")).to_contain_text("Found coins on 1")
    tab(page, "payments")
    row = page.locator(f'#payments li[data-id="{pid}"]')
    row.wait_for()
    assert "Expired, 5 received" in row.inner_text()


def test_api_key_rotation_hint_and_settings_protection(page, server, chain, tick):
    open_wallet(page)
    screen(page, "api-key-card")
    expect(page.locator("#key-info")).to_contain_text("Your current key starts with gw_")
    page.click("#new-key")
    page.click("#new-key")  # second click confirms
    page.wait_for_selector("#new-key-box:not([hidden])")
    new_key = page.input_value("#new-key-value")
    expect(page.locator("#key-info")).to_contain_text(f"starts with {new_key[:7]}… Last used: not yet")
    key = {"Authorization": "Bearer " + new_key}

    api = httpx.Client(base_url=server, headers=key)
    # This account has signed in to the wallet, so the API key alone can't change settings.
    for method, path in (("PUT", "/accounts/me/webhook"), ("DELETE", "/accounts/me/webhook"), ("POST", "/accounts/me/api-key")):
        r = api.request(method, path, json={"url": "https://shop.example/hook"} if method == "PUT" else None)
        assert r.status_code == 403, (path, r.status_code, r.text)

    # The webhook itself (set like the wallet would; its screen is plan item 2).
    import db
    from contextlib import closing
    with closing(db.connect()) as conn, conn:
        conn.execute("UPDATE accounts SET webhook_url = 'http://127.0.0.1:9/hook', webhook_secret = ?",
                     (db.seal("whsec_test", "webhook_secret"),))
    p = api.post("/payments", json={"chain": "ETH", "asset": "USDT", "amount": "12.5", "reference": "order-7"}).json()
    chain.pay("ETH", "USDT", p["address"], 12_500_000, tx="0x" + "dd" * 32)
    sent = tick()
    events = [json.loads(body) for _, body in sent if json.loads(body)["payment"]["id"] == p["id"]]
    assert [e["event"] for e in events] == ["payment.confirmed"], events
    assert events[0]["payment"]["transactions"][0]["url"] == "https://etherscan.io/tx/0x" + "dd" * 32
    assert events[0]["payment"]["reference"] == "order-7"

    page.reload()
    unlock(page)
    screen(page, "api-key-card")
    expect(page.locator("#key-info")).not_to_contain_text("not yet")  # the shop's key was used just now


def test_checkout_fits_a_phone(browser, server, page):
    open_wallet(page)
    pid = new_payment(page, "1", "TRX/USDT")
    phone = browser.new_context(base_url=server, viewport={"width": 375, "height": 812}, is_mobile=True).new_page()
    try:
        phone.goto(f"/pay/{pid}")
        phone.wait_for_selector("#payment:not([hidden])")
        assert phone.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "no sideways scrolling"
        assert "Tron (TRC-20)" in phone.locator("#network").inner_text()
    finally:
        phone.context.close()
