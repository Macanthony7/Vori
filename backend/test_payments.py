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
from xpubs import address  # noqa: E402
import app as app_module  # noqa: E402
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


def check_evm_send(key):
    """Native ETH send: every funded wallet address is a source with its own nonce; several signed
    transfers are relayed in order and a rejection part-way is reported, not hidden."""
    cc = app_module.chain_client
    real = (cc.evm_account, cc.evm_fees, cc.evm_broadcast)
    main = address("ETH", CASES["ETH"][0], 0)
    cc.evm_account = lambda chain, addr: (10**18, 4) if addr == main else (0, 0)
    cc.evm_fees = lambda chain: {"type": "eip1559", "normal": {"max_fee_per_gas": "30", "max_priority_fee_per_gas": "2"}}
    sent = []

    def relay(chain, tx):
        if tx == "ff" * 20:
            raise ChainError("nonce too low")
        sent.append(tx)
        return "0x" + str(len(sent)) * 64
    cc.evm_broadcast = relay
    try:
        prep = api.get("/wallet/send/eth", headers=key).json()
        assert prep["kind"] == "evm" and prep["chain_id"] == 1 and prep["gas_limit"] == 21000, prep
        assert prep["sources"] == [{"address": main, "index": 0, "balance": str(10**18), "nonce": 4}], prep  # empty addresses left out
        assert api.get("/wallet/send/bsc", headers=key).json()["chain_id"] == 56
        r = api.post("/wallet/send/ETH", json={"txs": ["0x" + "02" * 40, "ff" * 20, "03" * 40]}, headers=key).json()
        assert r["tx_hashes"] == ["0x" + "1" * 64] and "transaction 2 of 3" in r["error"] and sent == ["02" * 40], r
    finally:
        cc.evm_account, cc.evm_fees, cc.evm_broadcast = real


def check_wallet_login():
    """Signature login (what the wallet frame does), sessions, key rotation, balances."""
    from bip_utils import Bip44, Bip44Changes, Bip44Coins
    from coincurve import PrivateKey

    acct = Bip44.FromSeed(os.urandom(64), Bip44Coins.BITCOIN).Purpose().Coin().Account(0)
    xpub = acct.PublicKey().ToExtended()
    main_key = PrivateKey(acct.Change(Bip44Changes.CHAIN_EXT).AddressIndex(0).PrivateKey().Raw().ToBytes())
    r = api.post("/accounts", json={"xpubs": {"BTC": xpub}})
    assert r.status_code == 201, r.text
    old_key = {"Authorization": "Bearer " + r.json()["api_key"]}

    def sign(challenge, key=main_key):
        return key.sign((app_module.LOGIN_PREFIX + challenge).encode()).hex()  # DER over sha256, like bitcoinjs

    c = api.post("/auth/challenge").json()["challenge"]
    assert api.post("/auth/login", json={"xpub": xpub, "challenge": c, "signature": sign(c, PrivateKey())}).status_code == 401
    assert api.post("/auth/login", json={"xpub": xpub, "challenge": c, "signature": sign(c)}).status_code == 401, "challenge is single use"
    c = api.post("/auth/challenge").json()["challenge"]
    r = api.post("/auth/login", json={"xpub": xpub, "challenge": c, "signature": sign(c)})
    assert r.status_code == 200 and r.json()["token"].startswith("gws_"), r.text
    assert "no-store" in r.headers["cache-control"]
    sess = {"Authorization": "Bearer " + r.json()["token"]}
    assert api.get("/accounts/me", headers=sess).json()["chains"]["BTC"]["first_address"] == address("BTC", xpub, 0)
    assert api.post("/auth/login", json={"xpub": CASES["BTC"][0] + "x", "challenge": "nope", "signature": "00"}).status_code == 401

    # Key recovery: a session can replace the API key; the old key stops working.
    new_key = {"Authorization": "Bearer " + api.post("/accounts/me/api-key", headers=sess).json()["api_key"]}
    assert api.get("/accounts/me", headers=old_key).status_code == 401
    assert api.get("/accounts/me", headers=new_key).status_code == 200

    # Balances: main address always counted, plus funded payment addresses; failures marked incomplete.
    pay = api.post("/payments", json={"chain": "BTC", "asset": "BTC", "amount": "0.01"}, headers=sess).json()
    with closing(db.connect()) as conn, conn:
        conn.execute("UPDATE payments SET received = '1000000' WHERE id = ?", (pay["id"],))
    fake = {address("BTC", xpub, 0): 50_000, pay["address"]: 1_000_000}
    real_balance, app_module.chain_client.balance = app_module.chain_client.balance, lambda c, a, addr: fake[addr]
    try:
        b = api.get("/wallet/balances", headers=sess).json()["balances"]
        assert len(b) == 1 and b[0]["balance"] == "0.0105" and b[0]["complete"], b
        assert b[0]["main_address"] == address("BTC", xpub, 0) and len(b[0]["addresses"]) == 2, b
        app_module._balance_cache.clear()

        def flaky(c, a, addr):
            if addr == pay["address"]:
                raise ChainError("down")
            return fake[addr]
        app_module.chain_client.balance = flaky
        b = api.get("/wallet/balances", headers=sess).json()["balances"]
        assert b[0]["balance"] == "0.0005" and not b[0]["complete"], b
    finally:
        app_module.chain_client.balance = real_balance
        app_module._balance_cache.clear()

    # Send: the server lists spendable coins of every wallet address and relays the signed tx.
    cc = app_module.chain_client
    real = (cc.unspent, cc.fee_rates, cc.broadcast)
    relayed = []
    coins = {address("BTC", xpub, 0): [{"tx_hash": "aa" * 32, "vout": 1, "value": 5000, "confirmations": 3}],
             pay["address"]: [{"tx_hash": "bb" * 32, "vout": 0, "value": 1_000_000, "confirmations": 7}]}
    cc.unspent = lambda chain, addr: coins.get(addr, [])
    cc.fee_rates = lambda chain: {"slow": 2, "normal": 5, "fast": 12}
    cc.broadcast = lambda chain, tx: relayed.append((chain, tx)) or "cc" * 32
    try:
        r = api.get("/wallet/send/btc", headers=sess)
        prep = r.json()
        assert r.status_code == 200 and prep["fee_per_byte"]["normal"] == 5 and prep["change_index"] == 0, r.text
        assert sorted((u["index"], u["value"]) for u in prep["utxos"]) == [(0, 5000), (1, 1_000_000)], prep
        assert all(u["address"] == address("BTC", xpub, u["index"]) for u in prep["utxos"])
        assert api.get("/wallet/send/TRX", headers=sess).status_code == 400  # not supported yet
        assert api.get("/wallet/send/LTC", headers=sess).status_code == 400  # no LTC xpub
        assert api.post("/wallet/send/BTC", json={"txs": ["zz" * 20]}, headers=sess).status_code == 422
        r = api.post("/wallet/send/BTC", json={"txs": ["01" * 100]}, headers=sess)
        assert r.status_code == 200 and r.json()["tx_hashes"] == ["cc" * 32] and relayed == [("BTC", "01" * 100)], r.text

        def reject(chain, tx):
            raise ChainError("rejected: insufficient priority")
        cc.broadcast = reject
        r = api.post("/wallet/send/BTC", json={"txs": ["01" * 100]}, headers=sess)
        assert r.status_code == 502 and "insufficient priority" in r.text, r.text
        assert api.get("/wallet/send/BTC").status_code == 401
    finally:
        cc.unspent, cc.fee_rates, cc.broadcast = real

    assert api.delete("/auth/session", headers=sess).status_code == 204
    assert api.get("/accounts/me", headers=sess).status_code == 401, "logged out"


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
    # Index 0 is the main address (Receive / gas); payments start at index 1.
    assert btc["status"] == "pending" and btc["address"] == address("BTC", CASES["BTC"][0], 1) and btc["amount"] == "0.001", btc
    assert btc["address"] != CASES["BTC"][1]
    assert btc["confirmations_required"] == 2 and btc["reference"] == "order-1"
    btc2 = api.post("/payments", json={"chain": "btc", "asset": "btc", "amount": "1"}, headers=key).json()
    assert btc2["address"] != btc["address"], "second payment must get a new address"
    bsc = api.post("/payments", json={"chain": "BSC", "asset": "USDT", "amount": "25"}, headers=key).json()
    assert bsc["address"] == address("ETH", CASES["ETH"][0], 1) and bsc["token_contract"].startswith("0x55d398"), bsc
    trx = api.post("/payments", json={"chain": "TRX", "asset": "USDT", "amount": "10.5"}, headers=key).json()
    assert trx["address"] == address("TRX", CASES["TRX"][0], 1), trx

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

    check_wallet_login()
    check_evm_send(key)

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
