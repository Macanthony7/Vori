"""The redesign's promises: keyboard-only use, narrow screens (320 px), payments search and filter,
section tabs, and screenshot comparison of the checkout states against approved images.

Screenshots: golden images live in tests/e2e/goldens/<os>-<browser><major>/ (rendering differs between
machines and browsers). A missing golden is recorded and the test is skipped; delete a file to approve a
new look. Parts that change on every run (QR, address, countdown, ids) are masked.
"""
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from PIL import Image, ImageChops
from playwright.sync_api import expect

import rates
from conftest import PASSWORD, frame, import_wallet, new_payment, screen, set_password, tab, unlock

GOLDENS = Path(__file__).parent / "goldens"
MAX_DIFF = 0.004  # share of pixels allowed to differ (anti-aliasing noise), above it the look changed


def no_sideways_scroll(pg):
    return pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def compare(shot, name, browser):
    folder = GOLDENS / f"{sys.platform}-{browser.browser_type.name}{browser.version.split('.')[0]}"
    folder.mkdir(parents=True, exist_ok=True)
    golden = folder / f"{name}.png"
    if not golden.exists():
        golden.write_bytes(shot)
        pytest.skip(f"recorded new golden {golden.relative_to(GOLDENS.parent)}: check it, it's now the approved look")
    current = folder / f"{name}.actual.png"
    current.write_bytes(shot)
    a, b = Image.open(golden).convert("RGB"), Image.open(current).convert("RGB")
    assert a.size == b.size, f"{name}: size changed {a.size} -> {b.size} (see {current.name})"
    diff = ImageChops.difference(a, b).convert("L").point(lambda v: 255 if v > 24 else 0)
    changed = sum(1 for v in diff.getdata() if v) / (a.size[0] * a.size[1])
    assert changed <= MAX_DIFF, f"{name}: {changed:.2%} of pixels changed (see {current.name}; delete the golden to approve)"
    current.unlink()


def test_wallet_tabs_work_by_keyboard(page):
    import_wallet(page)
    unlock(page)
    page.focus("#tab-wallet")
    page.keyboard.press("ArrowRight")
    expect(page.locator("#panel-payments")).to_be_visible()
    expect(page.locator("#tab-payments")).to_be_focused()
    page.keyboard.press("End")
    expect(page.locator("#panel-settings")).to_be_visible()
    page.keyboard.press("ArrowRight")  # wraps around
    expect(page.locator("#panel-wallet")).to_be_visible()
    assert page.get_attribute("#tab-wallet", "aria-selected") == "true"
    assert page.locator("#panel-settings").is_hidden()
    # A settings row opens its screen by keyboard; Back returns to the list.
    tab(page, "settings")
    page.focus("[data-open=webhooks]")
    page.keyboard.press("Enter")
    expect(page.locator("#webhooks")).to_be_visible()
    page.locator("#webhooks [data-back]").focus()
    page.keyboard.press("Enter")
    expect(page.locator("#settings-home")).to_be_visible()


def test_payments_search_filter_and_empty_states(page, chain, tick):
    import_wallet(page)
    unlock(page)
    btc = new_payment(page, "0.002", "BTC/BTC")
    trx = new_payment(page, "7", "TRX/TRX")
    import db
    from contextlib import closing
    with closing(db.connect()) as conn:
        address = conn.execute("SELECT address FROM payments WHERE id = ?", (trx,)).fetchone()["address"]
    chain.pay("TRX", "TRX", address, 7_000_000)
    tick()
    page.reload()
    unlock(page)
    tab(page, "payments")
    rows = page.locator("#payments li")
    expect(rows.first).to_be_visible()
    page.fill("#pay-search", btc[-10:].lower())  # part of the id, any case
    expect(rows).to_have_count(1)
    expect(rows.first).to_contain_text("0.002 BTC")
    page.fill("#pay-search", "")
    page.click("#pay-filter [data-status=confirmed]")
    expect(page.locator("#payments li", has_text="7 TRX")).to_have_count(1)
    assert all("Confirmed" in t for t in rows.all_inner_texts())
    page.fill("#pay-search", "no-such-payment-anywhere")
    expect(page.locator("#payments-nomatch")).to_be_visible()
    expect(page.locator("#payments")).to_be_hidden()


def test_sign_up_by_keyboard_only(page):
    """Create a wallet without touching the mouse: Tab, Space and Enter only."""
    page.goto("/")
    page.keyboard.press("Tab")
    focused = page.evaluate("document.activeElement.dataset.mode")
    for _ in range(4):
        if focused == "create":
            break
        page.keyboard.press("Tab")
        focused = page.evaluate("document.activeElement.dataset.mode")
    assert focused == "create", "the first choices are reachable with Tab"
    page.keyboard.press("Enter")
    f = frame(page)
    f.locator("#words li").nth(11).wait_for()
    words = f.locator("#words li").all_inner_texts()
    f.locator("#c-ack").focus()
    page.keyboard.press("Space")
    page.keyboard.press("Tab")
    page.keyboard.press("Enter")  # Continue
    checks = f.locator("#c-checks fieldset")
    expect(checks.first.locator("input").first).to_be_focused()
    for i in range(3):  # each position is a radio group: arrows move to the right word, Tab to the next group
        word = words[int(checks.nth(i).get_attribute("data-pos"))].split()[-1]
        options = checks.nth(i).locator("label").all_inner_texts()
        if i:
            page.keyboard.press("Tab")
        page.keyboard.press("Space")  # select the focused (first) choice
        for _ in range(options.index(word)):
            page.keyboard.press("ArrowRight")
        expect(checks.nth(i).locator("input:checked")).to_have_value(word)
    page.keyboard.press("Tab")
    page.keyboard.press("Tab")  # past "Show phrase again" to Continue
    page.keyboard.press("Enter")
    expect(f.locator("#p-new")).to_be_focused()
    expect(f.locator("#p-steps-create")).to_be_visible()  # step 3 of 3 for a new phrase
    page.keyboard.type(PASSWORD)
    page.keyboard.press("Tab")
    page.keyboard.type(PASSWORD)
    page.keyboard.press("Enter")
    page.wait_for_selector("#result:not([hidden])", timeout=90_000)  # PBKDF2 + sign-up: slow on a busy machine


def test_focus_is_visible(page):
    page.goto("/")
    page.keyboard.press("Tab")
    style = page.evaluate("getComputedStyle(document.activeElement).outlineStyle")
    assert style == "solid", "keyboard focus has a visible ring"


@pytest.mark.parametrize("width", [320, 375])
def test_narrow_screens(browser, server, page, width, fixed_rates):
    import_wallet(page)
    unlock(page)
    pid = new_payment(page, "1", "TRX/USDT")
    phone = browser.new_context(base_url=server, viewport={"width": width, "height": 720}, is_mobile=True).new_page()
    try:
        phone.goto(f"/pay/{pid}")
        phone.wait_for_selector("#payment:not([hidden])")
        assert no_sideways_scroll(phone), f"checkout scrolls sideways at {width}px"
        qr = phone.locator("#qr canvas").bounding_box()
        assert qr["x"] >= 0 and qr["x"] + qr["width"] <= width, "the QR code fits"
        for path in ("/", "/xpub.html"):
            phone.goto(path)
            assert no_sideways_scroll(phone), f"{path} scrolls sideways at {width}px"
        phone.goto("/")  # the wallet on a phone, after setting it up there
        import_wallet(phone)
        unlock(phone)
        for section in ("wallet", "payments", "settings"):
            tab(phone, section)
            assert no_sideways_scroll(phone), f"wallet {section} scrolls sideways at {width}px"
        for name in ("shop-settings", "webhooks", "api-key-card", "integration"):
            screen(phone, name)
            assert no_sideways_scroll(phone), f"settings {name} scrolls sideways at {width}px"
        phone.click("#tab-wallet")
        phone.locator("#balances li").first.click()  # a coin screen with its chart
        phone.wait_for_selector("#chart svg")
        assert no_sideways_scroll(phone), f"coin screen scrolls sideways at {width}px"
        bar = phone.locator("#tabs").bounding_box()
        assert bar["x"] >= 0 and bar["x"] + bar["width"] <= width, "the tab bar fits"
    finally:
        phone.context.close()


@pytest.fixture
def fixed_rates(monkeypatch):
    usd = {"BTC": Decimal("60000"), "ETH": Decimal("2000"), "TRX": Decimal("0.30"), "USDT": Decimal(1)}
    monkeypatch.setattr(rates, "current", lambda currency: usd if currency == "USD" else {})


def test_checkout_states_look_as_approved(page, browser, server, chain, tick, fixed_rates):
    import_wallet(page)
    unlock(page)
    screen(page, "shop-settings")
    page.fill("#shop-name", "Corner Coffee")
    page.click("#shop-save")
    expect(page.locator("#shop-result")).to_contain_text("Saved")
    pid = new_payment(page, "10.5", "TRX/USDT")
    priced = new_payment(page, "25", "USD")

    import db
    from contextlib import closing
    with closing(db.connect()) as conn:
        address = conn.execute("SELECT address FROM payments WHERE id = ?", (pid,)).fetchone()["address"]

    def shoot(pg, name):
        pg.wait_for_timeout(300)  # let the strike animation settle (also disabled below)
        masks = [pg.locator(s) for s in ("#qr", "#address", "#countdown", "#payment-id")]
        compare(pg.screenshot(full_page=True, animations="disabled", mask=masks, mask_color="#7f7f7f"), name, browser)

    results = []
    for scheme in ("light", "dark"):
        pg = browser.new_context(base_url=server, viewport={"width": 420, "height": 900}, color_scheme=scheme).new_page()
        try:
            pg.goto(f"/pay/{priced}")
            pg.wait_for_selector("#choose:not([hidden])")
            for name, step in ((f"choose-{scheme}", None), (f"waiting-{scheme}", "wait")):
                if step == "wait":
                    pg.goto(f"/pay/{pid}")
                    pg.wait_for_selector("#payment:not([hidden])")
                try:
                    shoot(pg, name)
                except pytest.skip.Exception as e:
                    results.append(str(e))
        finally:
            pg.context.close()

    pg = browser.new_context(base_url=server, viewport={"width": 420, "height": 900}, color_scheme="light").new_page()
    try:
        pg.goto(f"/pay/{pid}")
        pg.wait_for_selector("#payment:not([hidden])")
        for name, units, confirm in (("partly-paid", 4_000_000, False), ("seen", 6_500_000, False), ("paid", 0, True)):
            if units:
                chain.pay("TRX", "USDT", address, units, confirmed=False)
            if confirm:
                chain.confirm("TRX", "USDT", address)
            tick()
            expect(pg.locator("#status")).to_contain_text({"partly-paid": "Received 4", "seen": "Payment received",
                                                           "paid": "Paid"}[name])
            if name == "seen":
                expect(pg.locator("#progress .mark").nth(1)).to_have_class("mark now")
            if name == "paid":
                expect(pg.locator("#progress .mark").nth(2)).to_have_class("mark done proven struck")
            try:
                shoot(pg, name)
            except pytest.skip.Exception as e:
                results.append(str(e))
    finally:
        pg.context.close()
    if results:
        pytest.skip("; ".join(results))
