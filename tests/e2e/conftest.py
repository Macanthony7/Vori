"""E2E fixtures: the real API (in this process, on a free port, temp database, fake chain) and a real
Chromium driven by Playwright. Run: python -m pytest tests/e2e   (docs/testing.md)
"""
import os
import socket
import sys
import tempfile
import threading
import time
from contextlib import closing
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).parent))

with socket.socket() as _s:
    _s.bind(("127.0.0.1", 0))
    PORT = _s.getsockname()[1]
BASE = f"http://127.0.0.1:{PORT}"

# Before any backend import: these are read at import time. Explicit values win over backend/.env.
os.environ.update({
    "GATEWAY_DB": os.path.join(tempfile.mkdtemp(), "e2e.db"),
    "GATEWAY_DB_KEY": os.urandom(32).hex(),
    "GATEWAY_PUBLIC_URL": BASE,
    "GATEWAY_TESTNETS": "0",
    "GATEWAY_TRUST_PROXY": "0",
    "GATEWAY_ALLOW_PRIVATE_WEBHOOKS": "1",
})

import uvicorn  # noqa: E402
from playwright.sync_api import expect, sync_playwright  # noqa: E402

expect.set_options(timeout=20_000)  # the checkout page polls every 5 s

import app as app_module  # noqa: E402
import db  # noqa: E402
import watcher  # noqa: E402
from fakechain import FakeChain  # noqa: E402

# The known test phrase (BIP39 test vectors) and its account-0 addresses, as in backend/test_xpubs.py.
PHRASE = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
PASSWORD = "correct horse battery staple"


@pytest.fixture(scope="session")
def chain():
    fake = FakeChain()
    fake.install()
    return fake


@pytest.fixture(autouse=True)
def clean_chain(chain):
    chain.reset()
    app_module._balance_cache.clear()


@pytest.fixture(scope="session")
def server(chain):
    db.init()
    app_module.LIMITS.update({k: (10**6, 60) for k in app_module.LIMITS})  # limits have their own tests
    srv = uvicorn.Server(uvicorn.Config(app_module.app, host="127.0.0.1", port=PORT, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield BASE
    srv.should_exit = True
    thread.join(5)


@pytest.fixture
def tick(chain):
    """Run one watcher pass against the fake chain. Returns the webhooks it delivered: [(url, body)]."""
    sent = []

    def run():
        with closing(db.connect()) as conn:
            # Due now: the watcher's per-chain pacing would otherwise skip payments checked a moment ago.
            with conn:
                conn.execute("UPDATE payments SET next_check_at = ?", (db.now(),))
            watcher.tick(conn, observe=chain.observe, lookup=chain.tx_hashes,
                         send=lambda url, secret, body: sent.append((url, body)))
        return sent
    return run


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        b = None
        # Playwright's own Chromium (CI installs it), else the Edge or Chrome already on this machine.
        for channel in (os.getenv("E2E_BROWSER_CHANNEL"), None, "msedge", "chrome"):
            try:
                b = p.chromium.launch(channel=channel) if channel else p.chromium.launch()
                break
            except Exception:
                continue
        if b is None:
            pytest.skip("no Chromium-based browser found (python -m playwright install chromium)")
        yield b
        b.close()


@pytest.fixture
def page(browser, server):
    """A fresh browser profile per test (its own localStorage, so its own saved wallet)."""
    context = browser.new_context(base_url=server)
    pg = context.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.set_default_timeout(20_000)
    yield pg
    context.close()
    assert not errors, f"JavaScript errors on the page: {errors}"


def frame(pg):
    return pg.frame_locator("#frame")


def import_wallet(pg, phrase=PHRASE, password=PASSWORD, account=0):
    """Onboarding with an existing phrase: returns the API key shown (None if it was a restore)."""
    pg.goto("/")
    pg.click("[data-mode=import]")
    f = frame(pg)
    f.locator("#i-phrase").fill(phrase)
    f.locator("#import summary").click()  # "Advanced": passphrase and account number
    f.locator("#i-account").fill(str(account))
    f.locator("#i-done").click()
    set_password(f, password)
    pg.wait_for_selector("#result:not([hidden]), #restored:not([hidden])")
    return pg.input_value("#api-key") if pg.is_visible("#result") else None


def set_password(f, password=PASSWORD):
    f.locator("#p-new").fill(password)
    f.locator("#p-again").fill(password)
    f.locator("#p-done").click()


def tab(pg, name):
    """Open a wallet tab (wallet, payments or settings) at its first screen."""
    pg.click(f"#tab-{name}")
    pg.wait_for_selector(f"#panel-{name}:not([hidden])")
    if pg.get_attribute(f"#tab-{name}", "aria-selected") == "true" and pg.is_hidden(
            f"#panel-{name} > .screen:first-child"):
        pg.click(f"#tab-{name}")  # tapping the open tab again goes back to its start
    pg.wait_for_selector(f"#panel-{name} > .screen:first-child:not([hidden])")


def screen(pg, name):
    """Open a Settings screen: shop-settings, api-key-card, webhooks or integration."""
    tab(pg, "settings")
    pg.click(f"[data-open={name}]")
    pg.wait_for_selector(f"#{name}:not([hidden])")


def open_coin(pg, network):
    """Open the coin screen for the balance row whose network is exactly `network` (e.g. "Bitcoin")."""
    import re
    tab(pg, "wallet")
    pg.locator("#balances li", has=pg.locator(".network", has_text=re.compile(f"^{re.escape(network)}$"))).click()
    pg.wait_for_selector("#coin:not([hidden])")


def new_payment(pg, amount, network):
    """New payment in the wallet for `amount` of "CHAIN/ASSET" (or a "USD"/"EUR" price). Returns its id."""
    tab(pg, "wallet")
    pg.click("#act-charge")
    link = pg.locator("#charge-result a")
    before = link.get_attribute("href") if link.count() and link.is_visible() else None
    pg.fill("#charge-amount", amount)
    if network in ("USD", "EUR"):
        pg.select_option("#charge-unit", network)
    else:
        pg.select_option("#charge-unit", "coin")
        pg.select_option("#charge-network", network)
    pg.click("#charge-create")
    if before:
        from playwright.sync_api import expect
        expect(link).not_to_have_attribute("href", before)
    link.wait_for()
    return link.get_attribute("href").split("/")[-1]


MARKET = {"BTC": ("60000", "0.0125"), "ETH": ("1700", "-0.021"), "LTC": ("44", "0.003"), "DOGE": ("0.057", None),
          "BNB": ("550", "0.01"), "TRX": ("0.30", "-0.004"), "USDT": ("1", "0")}


@pytest.fixture(autouse=True)
def fake_market(monkeypatch):
    """Fixed prices, 24 h changes and chart lines: the wallet's market data never reaches the internet in tests."""
    import math
    from decimal import Decimal
    import rates
    monkeypatch.setattr(rates, "market", lambda currency="USD": {
        c: {"price": Decimal(p), "change_24h": None if ch is None else Decimal(ch)} for c, (p, ch) in MARKET.items()})
    monkeypatch.setattr(rates, "history", lambda coin, span, currency="USD": [
        (1_700_000_000 + i * 900, Decimal(MARKET[coin][0]) * (1 + Decimal(str(round(0.03 * math.sin(i / 5) + 0.0006 * i, 6)))))
        for i in range(48)])


def unlock(pg, password=PASSWORD):
    pg.goto("/wallet.html")
    f = frame(pg)
    f.locator("#u-pass").fill(password)
    f.locator("#u-done").click()
    # 600,000 PBKDF2 rounds + signature sign-in: slow on a busy machine (coverage, parallel browsers).
    pg.wait_for_selector("#content:not([hidden])", timeout=90_000)
    pg.locator("#balances li .amount").first.wait_for(timeout=60_000)  # balances loaded, not "Checking…"
