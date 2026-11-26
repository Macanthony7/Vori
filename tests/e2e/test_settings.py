"""Dashboard settings: the Webhooks screen against a real local receiver, and the Integration screen."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from playwright.sync_api import expect

import webhooks
from conftest import import_wallet, tab, unlock


class Shop(BaseHTTPRequestHandler):
    """The merchant's server: records webhooks, answers with Shop.status."""
    received, status = [], 200

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"])).decode()
        Shop.received.append((self.headers["Gateway-Signature"], body))
        self.send_response(Shop.status)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def shop():
    srv = HTTPServer(("127.0.0.1", 0), Shop)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    Shop.received.clear()
    Shop.status = 200
    yield f"http://127.0.0.1:{srv.server_port}/hooks/crypto"
    srv.shutdown()


def open_section(page, section):
    tab(page, {"webhooks": "settings", "integration": "integration"}[section])


def test_webhook_screen_save_test_deliveries_remove(page, shop, chain, tick):
    import_wallet(page)
    unlock(page)
    open_section(page, "webhooks")
    expect(page.locator("#hook-current")).to_contain_text("No webhook set")

    page.fill("#hook-url", "ftp://nope")
    page.click("#hook-save")
    expect(page.locator("#hook-error")).to_contain_text("http")

    page.fill("#hook-url", shop)
    page.click("#hook-save")
    expect(page.locator("#hook-secret-box")).to_be_visible()
    secret = page.input_value("#hook-secret")
    assert secret.startswith("whsec_")
    expect(page.locator("#hook-current")).to_contain_text(shop)

    page.click("#hook-test")
    expect(page.locator("#hook-result")).to_contain_text("test event delivered")
    sig, body = Shop.received[-1]
    assert webhooks.verify(secret, body, sig), "signed with the secret the screen showed"
    assert json.loads(body)["event"] == "payment.test"

    Shop.status = 500
    page.click("#hook-test")
    expect(page.locator("#hook-result")).to_contain_text("Not delivered: HTTP 500")

    # A real event shows up under Recent deliveries.
    tab(page, "overview")
    page.fill("#charge-amount", "2")
    page.select_option("#charge-network", "TRX/USDT")
    page.click("#charge-create")
    pid = page.locator("#charge-result a").get_attribute("href").split("/")[-1]
    import db
    from contextlib import closing
    with closing(db.connect()) as conn:
        address = conn.execute("SELECT address FROM payments WHERE id = ?", (pid,)).fetchone()["address"]
    chain.pay("TRX", "USDT", address, 2_000_000)
    tick()
    page.reload()
    unlock(page)
    open_section(page, "webhooks")
    row = page.locator("#hook-deliveries tbody tr", has_text=pid)
    expect(row).to_contain_text("payment.confirmed")

    page.click("#hook-remove")
    expect(page.locator("#hook-current")).to_contain_text("No webhook set")
    expect(page.locator("#hook-test")).to_be_hidden()


def test_integration_screen_shows_tested_samples(page, server):
    import_wallet(page)
    unlock(page)
    open_section(page, "integration")
    expect(page.locator("#api-base")).to_have_text(server)
    code = page.locator("#sample code")
    expect(code).to_contain_text("def create_payment")
    expect(code).to_contain_text(f'"GATEWAY_URL", "{server}"')  # the sample points at this gateway
    page.get_by_role("button", name="Node.js").click()
    expect(code).to_contain_text("crypto.timingSafeEqual")
    expect(page.get_by_role("button", name="Node.js")).to_have_attribute("aria-pressed", "true")
    expect(page.get_by_role("button", name="Python")).to_have_attribute("aria-pressed", "false")
    page.get_by_role("button", name="PHP").click()
    expect(code).to_contain_text("hash_equals")
    page.get_by_role("button", name="curl").click()
    expect(code).to_contain_text("curl -sS -X POST")
