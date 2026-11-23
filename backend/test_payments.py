"""Self-check for payments, watcher and webhooks: python test_payments.py (from backend/).

Uses a throwaway database, a fake blockchain and a real local HTTP server as the webhook receiver.
No network or provider keys needed.
"""
import json
import os
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

os.environ["GATEWAY_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["GATEWAY_ALLOW_PRIVATE_WEBHOOKS"] = "1"  # so the local receiver below is allowed

from contextlib import closing  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

import db  # noqa: E402
import payments  # noqa: E402
import watcher  # noqa: E402
import webhooks  # noqa: E402
from assets import AmountError, from_units, to_units  # noqa: E402
from chain_client import ChainError  # noqa: E402
from test_xpubs import CASES  # noqa: E402
from app import app  # noqa: E402

api = TestClient(app)


class Receiver(BaseHTTPRequestHandler):
    calls, status = [], 200

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"])).decode()
        Receiver.calls.append((self.headers["Gateway-Signature"], body))
        self.send_response(Receiver.status)
        self.end_headers()

    def log_message(self, *args):
        pass


def check_amounts():
    assert to_units("12.50", 6) == 12_500_000
    assert to_units("100", 18) == 100 * 10**18  # bigger than SQLite's int64: stored as text
    assert from_units(1_500_000_000_000_000_000, 18) == "1.5" and from_units(0, 8) == "0"
    for bad in ("0", "-1", "abc", "1.0000001", "NaN", "1e30"):
        try:
            to_units(bad, 6)
        except AmountError:
            continue
        raise AssertionError(f"accepted amount {bad!r}")
    try:
        to_units(1.5, 6)
        raise AssertionError("accepted a float")
    except AmountError:
        pass


def check_webhook_urls():
    webhooks.ALLOW_PRIVATE = False
    try:
        for bad in ("http://example.com/hook", "https://127.0.0.1/hook", "https://10.0.0.5/x",
                    "https://169.254.169.254/latest", "https://user:pw@example.com/", "ftp://x"):
            try:
                webhooks.check_url(bad)
            except webhooks.WebhookError:
                continue
            raise AssertionError(f"accepted webhook url {bad}")
    finally:
        webhooks.ALLOW_PRIVATE = True
    sig = webhooks.sign("s3cret", '{"a":1}', 1_700_000_000)
    assert webhooks.verify("s3cret", '{"a":1}', sig, now=1_700_000_010)
    assert not webhooks.verify("s3cret", '{"a":2}', sig, now=1_700_000_010)       # body changed
    assert not webhooks.verify("other", '{"a":1}', sig, now=1_700_000_010)        # wrong secret
    assert not webhooks.verify("s3cret", '{"a":1}', sig, now=1_700_000_000 + 900)  # too old


def demo():
    check_amounts()
    check_webhook_urls()

    server = HTTPServer(("127.0.0.1", 0), Receiver)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    hook_url = f"http://127.0.0.1:{server.server_port}/hook"

    # --- account + API -----------------------------------------------------------------------------
    r = api.post("/accounts", json={"xpubs": {c: CASES[c][0] for c in ("BTC", "ETH", "TRX")}})
    assert r.status_code == 201, r.text
    key = {"Authorization": "Bearer " + r.json()["api_key"]}
    other = {"Authorization": "Bearer " + api.post("/accounts", json={"xpubs": {"DOGE": CASES["DOGE"][0]}}).json()["api_key"]}

    r = api.put("/accounts/me/webhook", json={"url": hook_url}, headers=key)
    assert r.status_code == 200 and r.json()["secret"].startswith("whsec_"), r.text
    secret = r.json()["secret"]

    btc = api.post("/payments", json={"chain": "BTC", "asset": "BTC", "amount": "0.001", "reference": "order-1"}, headers=key).json()
    assert btc["status"] == "pending" and btc["address"] == CASES["BTC"][1] and btc["amount"] == "0.001", btc
    assert btc["confirmations_required"] == 2 and btc["reference"] == "order-1"
    btc2 = api.post("/payments", json={"chain": "btc", "asset": "btc", "amount": "1"}, headers=key).json()
    assert btc2["address"] != btc["address"], "second payment must get a new address"
    bsc = api.post("/payments", json={"chain": "BSC", "asset": "USDT", "amount": "25"}, headers=key).json()
    assert bsc["address"] == CASES["ETH"][1] and bsc["token_contract"].startswith("0x55d398"), bsc  # ETH xpub, index 0
    trx = api.post("/payments", json={"chain": "TRX", "asset": "USDT", "amount": "10.5"}, headers=key).json()
    assert trx["address"] == CASES["TRX"][1], trx

    expect = [
        ({"chain": "LTC", "asset": "LTC", "amount": "1"}, key, 400, "no LTC xpub"),
        ({"chain": "BTC", "asset": "USDT", "amount": "1"}, key, 400, "unsupported"),
        ({"chain": "TRX", "asset": "USDT", "amount": "1.1234567"}, key, 400, "decimal places"),
        ({"chain": "BTC", "asset": "BTC", "amount": "0"}, key, 400, "greater than 0"),
        ({"chain": "BTC", "asset": "BTC", "amount": "1", "reference": "x" * 101}, key, 400, "reference"),
        ({"chain": "BTC", "asset": "BTC", "amount": "1"}, {}, 401, "missing API key"),
    ]
    for body, headers, status, fragment in expect:
        r = api.post("/payments", json=body, headers=headers)
        assert r.status_code == status and fragment in r.text, (body, r.status_code, r.text)
    assert api.get(f"/payments/{btc['id']}", headers=other).status_code == 404  # not your payment
    assert len(api.get("/payments?status=pending", headers=key).json()["payments"]) == 4

    # --- checkout page (public, no API key) -----------------------------------------------------------
    assert trx["checkout_url"].endswith("/pay/" + trx["id"]), trx
    r = api.get(f"/pay/{trx['id']}/status")
    co = r.json()
    assert r.status_code == 200 and co["address"] == trx["address"] and co["amount_due"] == "10.5", co
    assert "reference" not in co and "checkout_url" not in co and co["server_time"].endswith("Z"), co
    assert "no-store" in r.headers["cache-control"]
    assert api.get(f"/pay/{btc['id']}/status").json().keys() >= {"status", "expires_at"}
    assert "reference" not in api.get(f"/pay/{btc['id']}/status").text  # merchant's order data stays private
    assert api.get("/pay/pay_doesnotexist/status").status_code == 404
    r = api.get(f"/pay/{trx['id']}")
    assert r.status_code == 200 and "/pay.js" in r.text, r.status_code

    # --- watcher with a fake chain -----------------------------------------------------------------
    chain = {}  # address -> (seen, confirmed) in smallest units
    down = set()

    def observe(c, asset, address, confirmations):
        if address in down:
            raise ChainError("provider down")
        return chain.get((c, asset, address), (0, 0))

    t0 = datetime.now(timezone.utc)
    step = lambda minutes: t0 + timedelta(minutes=minutes)
    with closing(db.connect()) as conn:
        run = lambda when: watcher.tick(conn, now=when, observe=observe)

        run(step(1))  # nothing paid yet
        status = lambda p: api.get(f"/payments/{p['id']}", headers=key).json()
        assert status(btc)["status"] == "pending" and not Receiver.calls

        chain[("BTC", "BTC", btc["address"])] = (100_000, 0)       # full amount seen, 0 confirmations
        chain[("TRX", "USDT", trx["address"])] = (5_000_000, 5_000_000)  # half of 10.5 USDT
        down.add(bsc["address"])                                   # BSC provider failing
        run(step(3))
        assert status(btc)["status"] == "confirming" and status(btc)["amount_received"] == "0.001"
        assert status(trx)["status"] == "pending" and status(trx)["amount_received"] == "5"
        assert len(Receiver.calls) == 1
        sig, body = Receiver.calls[0]
        assert webhooks.verify(secret, body, sig), "webhook signature must verify"
        assert json.loads(body)["event"] == "payment.confirming" and json.loads(body)["payment"]["id"] == btc["id"]

        chain[("BTC", "BTC", btc["address"])] = (100_000, 100_000)
        run(step(5))
        s = status(btc)
        assert s["status"] == "confirmed" and s["confirmed_at"] and s["amount_confirmed"] == "0.001", s
        assert json.loads(Receiver.calls[-1][1])["event"] == "payment.confirmed"

        run(step(31))  # past the 30-minute window
        assert status(trx)["status"] == "expired" and status(trx)["amount_received"] == "5"  # underpaid
        assert status(btc2)["status"] == "expired"
        assert status(bsc)["status"] == "pending", "never expire a payment we couldn't observe"
        down.clear()
        run(step(32))
        assert status(bsc)["status"] == "expired"
        assert status(btc)["status"] == "confirmed", "final states never change"

        # Paid in time but never confirmed: watched for 24h after the window, then expired.
        late = payments.public(payments.create(conn, 1, "ETH", "ETH", "0.5", now=step(40)))
        chain[("ETH", "ETH", late["address"])] = (5 * 10**17, 0)
        run(step(41))
        assert status(late)["status"] == "confirming"
        run(step(40 + 30 + 24 * 60 + 1))
        assert status(late)["status"] == "expired"

        # Webhook retries: receiver failing -> retried later, then delivered.
        Receiver.status, before = 500, len(Receiver.calls)
        p = payments.public(payments.create(conn, 1, "TRX", "TRX", "1", now=step(2000)))
        chain[("TRX", "TRX", p["address"])] = (10**6, 10**6)
        run(step(2001))
        ev = conn.execute("SELECT * FROM webhook_events WHERE payment_id = ?", (p["id"],)).fetchone()
        assert ev["attempts"] == 1 and ev["delivered_at"] is None and ev["last_error"] == "HTTP 500", dict(ev)
        Receiver.status = 200
        run(step(2001.5))  # too early for the retry (60 s)
        assert len(Receiver.calls) == before + 1
        run(step(2003))
        ev = conn.execute("SELECT * FROM webhook_events WHERE payment_id = ?", (p["id"],)).fetchone()
        assert ev["attempts"] == 2 and ev["delivered_at"] and ev["next_attempt_at"] is None, dict(ev)

        # --- address reuse (gap limit) -------------------------------------------------------------
        # btc2 expired unpaid at step(30). Within 24h: a new address. After: btc2's address again.
        fresh = payments.create(conn, 1, "BTC", "BTC", "1", now=step(60))
        assert fresh["address"] not in (btc["address"], btc2["address"])
        reused = payments.create(conn, 1, "BTC", "BTC", "1", now=step(30 + 24 * 60 + 1))
        assert reused["address"] == btc2["address"], "expired-unpaid address should be reused after 24h"
        # trx (underpaid) must never be reused; nor may an address move to another asset.
        again = payments.create(conn, 1, "TRX", "USDT", "1", now=step(5000))
        assert again["address"] != trx["address"], "an address that received funds must not be reused"
        bnb = payments.create(conn, 1, "BSC", "BNB", "1", now=step(5000))
        assert bnb["address"] != bsc["address"], "reuse only for the same chain/asset"

        # Two watchers applying the same transition from the same stale row: one webhook, not two.
        stale = payments.create(conn, 1, "BTC", "BTC", "1", now=step(6000))
        for _ in range(2):
            payments.apply_observation(conn, stale, 10**8, 10**8, step(6001))
        n = conn.execute("SELECT COUNT(*) FROM webhook_events WHERE payment_id = ?", (stale["id"],)).fetchone()[0]
        assert n == 1, f"duplicate webhooks for one transition: {n}"

    server.shutdown()
    print("payments self-check OK")


if __name__ == "__main__":
    demo()
