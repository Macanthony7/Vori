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
os.environ["GATEWAY_DB_KEY"] = os.urandom(32).hex()  # never touch backend/.env from tests
os.environ["GATEWAY_ALLOW_PRIVATE_WEBHOOKS"] = "1"
os.environ["GATEWAY_TESTNETS"] = "0"  # explicit: backend/.env on a dev machine may turn them on  # so the local receiver below is allowed

from contextlib import closing  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

import db  # noqa: E402
import payments  # noqa: E402
import watcher  # noqa: E402
import webhooks  # noqa: E402
import rates  # noqa: E402
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


def check_token_send(key):
    """USDT and TRX: the main address is listed for gas, only token-holding addresses are sources,
    the destination is required, and top-ups can be followed with /wallet/tx."""
    cc = app_module.chain_client
    names = ("evm_account", "evm_fees", "token_balance", "evm_token_gas", "tron_params", "tron_block",
             "tron_account", "observe", "tron_token_energy", "tron_broadcast", "tx_status")
    real = {n: getattr(cc, n) for n in names}
    eth_main, trx_main = address("ETH", CASES["ETH"][0], 0), address("TRX", CASES["TRX"][0], 0)
    to_trx = address("TRX", CASES["TRX"][0], 9)
    cc.evm_account = lambda chain, a: (10**17, 5)
    cc.evm_fees = lambda chain: {"type": "eip1559", "normal": {"max_fee_per_gas": "30", "max_priority_fee_per_gas": "2"}}
    cc.token_balance = lambda chain, contract, a: 0  # nobody holds USDT yet
    cc.evm_token_gas = lambda *a: 50000
    cc.tron_params = lambda chain: {"bandwidth_fee": 1000, "energy_fee": 100, "create_fee": 1100000}
    cc.tron_block = lambda chain: {"number": 1, "timestamp": 2, "tx_trie_root": "00" * 32, "parent_hash": "00" * 32,
                                   "witness_address": "41" + "00" * 20, "version": 1}
    cc.tron_account = lambda chain, a: {"balance": 7_000_000 if a == trx_main else 0, "active": a == trx_main, "bandwidth": 600, "energy": 0}
    cc.observe = lambda chain, asset, a, n: (2_000_000 if a == trx_main else 0, 0)
    cc.tron_token_energy = lambda *a: 65000
    cc.tron_broadcast = lambda chain, tx: "ab" * 32
    cc.tx_status = lambda chain, h: True
    try:
        r = api.get("/wallet/send/eth?asset=usdt", headers=key)
        assert r.status_code == 400 and "`to`" in r.text, r.text  # gas depends on the destination
        prep = api.get("/wallet/send/ETH?asset=USDT&to=0x" + "11" * 20, headers=key).json()
        assert prep["asset"] == "USDT" and prep["main"]["address"] == eth_main and prep["sources"] == [], prep
        assert api.get("/wallet/send/TRX?to=Tnotanaddress", headers=key).status_code == 400
        prep = api.get(f"/wallet/send/TRX?to={to_trx}", headers=key).json()
        assert prep["kind"] == "tron" and prep["to_active"] is False and prep["params"]["create_fee"] == 1100000, prep
        assert prep["sources"] == [{"address": trx_main, "index": 0, "balance": "7000000", "active": True, "bandwidth": 600, "energy": 0}], prep
        prep = api.get(f"/wallet/send/TRX?asset=USDT&to={to_trx}", headers=key).json()
        assert [(s["address"], s["token_balance"], s["token_energy"]) for s in prep["sources"]] == [(trx_main, "2000000", 65000)], prep
        assert api.get("/wallet/send/TRX?asset=DOGE&to=" + to_trx, headers=key).status_code == 400
        r = api.post("/wallet/send/TRX", json={"txs": ["0a" + "00" * 20]}, headers=key).json()
        assert r["tx_hashes"] == ["ab" * 32], r
        assert api.get("/wallet/tx/TRX/" + "ab" * 32, headers=key).json() == {"done": True, "ok": True}
        assert api.get("/wallet/tx/TRX/xyz", headers=key).status_code == 422
        assert api.get("/wallet/tx/BTC/" + "ab" * 32, headers=key).status_code == 400
    finally:
        for n, f in real.items():
            setattr(cc, n, f)


def check_shop_and_idempotency():
    """Shop name and logo on the checkout page, return_url, and Idempotency-Key on POST /payments."""
    import base64
    import threading
    from bip_utils import Bip44, Bip44Coins
    xpub = Bip44.FromSeed(os.urandom(64), Bip44Coins.BITCOIN).Purpose().Coin().Account(0).PublicKey().ToExtended()
    key = {"Authorization": "Bearer " + api.post("/accounts", json={"xpubs": {"BTC": xpub}}).json()["api_key"]}
    png = b"\x89PNG\r\n\x1a\n" + os.urandom(200)
    data = lambda kind, raw: f"data:{kind};base64," + base64.b64encode(raw).decode()

    shop = lambda body: api.put("/accounts/me/shop", json=body, headers=key)
    assert shop({"name": "x" * 61}).status_code == 400
    assert shop({"name": "Two\nlines"}).status_code == 400
    for bad in (data("image/gif", b"GIF89a"), data("image/svg+xml", b"<svg onload=alert(1)>"),
                data("image/png", b"not a png"), data("image/png", b"\x89PNG\r\n\x1a\n" + b"0" * 100_001),
                "data:image/png;base64,***", "https://evil.example/logo.png"):
        r = shop({"name": "Shop", "logo": bad})
        assert r.status_code == 400, (bad[:40], r.text)
    assert shop({"name": "  Corner Café  ", "logo": data("image/png", png)}).json() == {"shop_name": "Corner Café", "has_logo": True}
    assert shop({"name": "Corner Café"}).json()["has_logo"] is True, "logo left out = kept"
    me = api.get("/accounts/me", headers=key).json()
    assert me["shop_name"] == "Corner Café" and me["logo"] == data("image/png", png)

    p = api.post("/payments", json={"chain": "BTC", "asset": "BTC", "amount": "0.01",
                                    "return_url": "https://shop.example/order/7?paid=1"}, headers=key).json()
    assert p["return_url"] == "https://shop.example/order/7?paid=1"
    co = api.get(f"/pay/{p['id']}/status").json()
    assert co["shop_name"] == "Corner Café" and co["logo_url"] == f"/pay/{p['id']}/logo" and co["return_url"] == p["return_url"]
    logo = api.get(co["logo_url"])
    assert logo.content == png and logo.headers["content-type"] == "image/png"
    assert logo.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'self'" and logo.headers["x-content-type-options"] == "nosniff"
    assert shop({"name": "", "logo": None}).json() == {"shop_name": None, "has_logo": False}
    assert api.get(co["logo_url"]).status_code == 404
    assert api.get(f"/pay/{p['id']}/status").json()["shop_name"] is None

    for bad in ("javascript:alert(1)", "data:text/html,hi", "ftp://shop.example", "http://shop.example/x",
                "https://user:pw@shop.example", 'https://shop.example/"onmouseover=x', "https://", "x" * 501):
        r = api.post("/payments", json={"chain": "BTC", "asset": "BTC", "amount": "1", "return_url": bad}, headers=key)
        assert r.status_code == 400 and "return_url" in r.text, (bad, r.text)
    assert api.post("/payments", json={"chain": "BTC", "asset": "BTC", "amount": "1", "return_url": "http://localhost:3000/x"},
                    headers=key).status_code == 201, "http is fine for a shop developed on localhost"

    # Idempotency-Key: retries get the same payment; the same key for another order is refused.
    order = {"chain": "BTC", "asset": "BTC", "amount": "0.5", "reference": "order-55"}
    post = lambda body, k: api.post("/payments", json=body, headers={**key, "Idempotency-Key": k})
    first = post(order, "order-55-try")
    again = post(order, "order-55-try")
    assert first.status_code == 201 and again.status_code == 200 and again.headers["idempotent-replayed"] == "true"
    assert again.json()["id"] == first.json()["id"] and again.json()["address"] == first.json()["address"]
    r = post({**order, "amount": "5"}, "order-55-try")
    assert r.status_code == 409 and "different payment" in r.text
    assert post(order, "another-key").json()["id"] != first.json()["id"]
    assert post(order, "").status_code == 400 and post(order, "k" * 101).status_code == 400
    with closing(db.connect()) as conn, conn:
        conn.execute("UPDATE idempotency_keys SET created_at = '2000-01-01T00:00:00Z' WHERE key = 'order-55-try'")
    assert post(order, "order-55-try").json()["id"] != first.json()["id"], "keys expire after 24 h"

    # Two identical retries at the same moment: one payment, not two.
    acct_id = api.get("/accounts/me", headers=key).json()["account_id"]
    gate, ids = threading.Barrier(2), []

    def racer():
        with closing(db.connect()) as conn:
            gate.wait()
            ids.append(payments.create(conn, acct_id, "BTC", "BTC", "1", idempotency={"key": "race", "hash": "h"})["id"])
    threads = [threading.Thread(target=racer) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(ids) == 2 and ids[0] == ids[1], ids


def check_price_requests():
    """Prices in USD/EUR: validation, the coins offered, choosing (rate locked, same id), races, expiry."""
    import threading
    from decimal import Decimal
    from bip_utils import Bip44, Bip44Coins
    seed = os.urandom(64)
    xpub = lambda coin: Bip44.FromSeed(seed, coin).Purpose().Coin().Account(0).PublicKey().ToExtended()
    key = {"Authorization": "Bearer " + api.post("/accounts", json={"xpubs": {
        "BTC": xpub(Bip44Coins.BITCOIN), "TRX": xpub(Bip44Coins.TRON)}}).json()["api_key"]}
    real_current = rates.current
    rates.current = lambda currency: {"BTC": Decimal("60000"), "TRX": Decimal("0.30")} if currency == "USD" else {}
    try:
        new = lambda body, h=key: api.post("/payments", json=body, headers=h)
        for bad, why in (({"price": "0", "currency": "USD"}, "price"), ({"price": "1.234", "currency": "USD"}, "price"),
                         ({"price": "abc", "currency": "USD"}, "price"), ({"price": "-5", "currency": "USD"}, "price"),
                         ({"price": "NaN", "currency": "USD"}, "price"), ({"price": "2000000", "currency": "USD"}, "price"),
                         ({"price": "10", "currency": "GBP"}, "currency"), ({"price": "10", "currency": "USD", "accept": []}, "accept"),
                         ({"price": "10", "currency": "USD", "accept": ["ETH/ETH"]}, "ETH/ETH"),  # no ETH xpub
                         ({"price": "10", "currency": "USD", "accept": ["BTC/DOGE"]}, "BTC/DOGE"),
                         ({"price": "10", "currency": "USD", "chain": "BTC", "asset": "BTC", "amount": "1"}, "not both"),
                         ({"chain": "BTC"}, "required")):
            r = new(bad)
            assert r.status_code == 400 and why in r.text, (bad, r.text)

        r = new({"price": "25.00", "currency": "usd", "reference": "order-9", "return_url": "https://shop.example/9"})
        req = r.json()
        assert r.status_code == 201 and req["status"] == "choosing" and req["chain"] is None and req["address"] is None, req
        assert req["price"] == "25.00" and req["currency"] == "USD" and set(req["accept"]) == {"BTC/BTC", "TRX/TRX", "TRX/USDT"}
        assert api.get(f"/payments/{req['id']}", headers=key).json()["status"] == "choosing"
        assert req["id"] in [p["id"] for p in api.get("/payments", headers=key).json()["payments"]], "listed while choosing"
        assert [p["id"] for p in api.get("/payments?status=choosing", headers=key).json()["payments"]] == [req["id"]]

        co = api.get(f"/pay/{req['id']}/status").json()
        assert co["status"] == "choosing" and "reference" not in co and co["price"] == "25.00", co
        opts = {(o["chain"], o["asset"]): o for o in co["options"]}
        assert opts[("BTC", "BTC")] == {"chain": "BTC", "asset": "BTC", "amount": "0.00041667", "available": True}
        assert opts[("TRX", "TRX")]["amount"] == "83.333334", "rounded up: never less than the price"
        assert opts[("TRX", "USDT")] == {"chain": "TRX", "asset": "USDT", "amount": None, "available": False}

        choose = lambda body, pid=req["id"]: api.post(f"/pay/{pid}/choose", json=body)
        assert choose({"chain": "ETH", "asset": "ETH"}).status_code == 400, "not accepted for this payment"
        assert choose({"chain": "TRX", "asset": "USDT"}).status_code == 503, "no rate: can't be chosen"
        assert choose({"chain": "BTC", "asset": "BTC"}, "pay_nope").status_code == 404
        r = choose({"chain": "btc", "asset": "btc"})
        paid = r.json()
        assert r.status_code == 200 and paid["id"] == req["id"] and paid["status"] == "pending", paid
        assert paid["amount"] == "0.00041667" and paid["address"].startswith("1") and paid["rate"] == "60000"
        assert paid["price"] == "25.00" and paid["return_url"] == "https://shop.example/9"
        assert choose({"chain": "TRX", "asset": "TRX"}).status_code == 409, "the coin can't be changed afterwards"
        full = api.get(f"/payments/{req['id']}", headers=key).json()
        assert full["chain"] == "BTC" and full["reference"] == "order-9" and full["currency"] == "USD"
        assert Decimal(full["amount"]) * Decimal(full["rate"]) >= Decimal("25")
        assert [p["id"] for p in api.get("/payments", headers=key).json()["payments"]].count(req["id"]) == 1

        # Two payers (two tabs) choosing at the same moment: one payment, one address used.
        race = new({"price": "5", "currency": "USD", "accept": ["TRX/TRX"]}).json()
        with closing(db.connect()) as conn:
            before = conn.execute("SELECT next_index FROM account_xpubs WHERE chain = 'TRX' AND account_id = "
                                  "(SELECT account_id FROM price_requests WHERE id = ?)", (race["id"],)).fetchone()[0]
        gate, results = threading.Barrier(2), []

        def racer():
            with closing(db.connect()) as conn:
                gate.wait()
                try:
                    results.append(payments.choose(conn, race["id"], "TRX", "TRX")["id"])
                except payments.ChoiceError as e:
                    results.append(e.status)
        threads = [threading.Thread(target=racer) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(results, key=str) == sorted([race["id"], 409], key=str), results
        with closing(db.connect()) as conn:
            after = conn.execute("SELECT next_index FROM account_xpubs WHERE chain = 'TRX' AND account_id = "
                                 "(SELECT account_id FROM price_requests WHERE id = ?)", (race["id"],)).fetchone()[0]
        assert after == before + 1, "the losing choice didn't use up an address"

        # Not chosen in time.
        late = new({"price": "5", "currency": "USD"}).json()
        with closing(db.connect()) as conn, conn:
            conn.execute("UPDATE price_requests SET expires_at = '2000-01-01T00:00:00Z' WHERE id = ?", (late["id"],))
        assert api.get(f"/pay/{late['id']}/status").json()["status"] == "expired"
        assert api.get(f"/pay/{late['id']}/status").json()["options"] == []
        assert choose({"chain": "BTC", "asset": "BTC"}, late["id"]).status_code == 410

        # Retries of a priced request return the same one.
        h = {**key, "Idempotency-Key": "priced-1"}
        a = api.post("/payments", json={"price": "7", "currency": "USD"}, headers=h)
        b = api.post("/payments", json={"price": "7", "currency": "USD"}, headers=h)
        assert a.status_code == 201 and b.status_code == 200 and a.json()["id"] == b.json()["id"]
        assert api.post("/payments", json={"price": "8", "currency": "USD"}, headers=h).status_code == 409
    finally:
        rates.current = real_current


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

    # Before any wallet sign-in (like an xpub-only account), the API key may change settings.
    me = api.get("/accounts/me", headers=old_key).json()
    assert me["api_key_hint"] == old_key["Authorization"][7:14] and me["settings_need_wallet"] is False, me
    assert api.get("/accounts/me", headers=old_key).json()["api_key_used_at"], "API key use is recorded"
    hook = {"url": "https://shop.example/hook"}
    assert api.put("/accounts/me/webhook", json=hook, headers=old_key).status_code == 200

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

    # After the first wallet sign-in, a leaked API key can't lock the merchant out or silence webhooks.
    for method, path, body in (("put", "/accounts/me/webhook", hook), ("delete", "/accounts/me/webhook", None),
                               ("post", "/accounts/me/api-key", None), ("put", "/accounts/me/shop", {"name": "Evil"})):
        r = api.request(method.upper(), path, json=body, headers=old_key)
        assert r.status_code == 403 and "wallet sign-in" in r.text, (path, r.status_code, r.text)
    assert api.get("/accounts/me", headers=old_key).json()["settings_need_wallet"] is True
    assert api.get("/payments", headers=old_key).status_code == 200, "the API key still works for payments"
    assert api.put("/accounts/me/webhook", json=hook, headers=sess).status_code == 200, "the wallet session can"
    assert api.delete("/accounts/me/webhook", headers=sess).status_code == 204

    # Key recovery: a session can replace the API key; the old key stops working.
    new_key = {"Authorization": "Bearer " + api.post("/accounts/me/api-key", headers=sess).json()["api_key"]}
    assert api.get("/accounts/me", headers=old_key).status_code == 401
    me = api.get("/accounts/me", headers=sess).json()
    assert me["api_key_hint"] == new_key["Authorization"][7:14] and me["api_key_used_at"] is None, me
    assert api.get("/accounts/me", headers=new_key).status_code == 200
    # "Last used" is written at most once a minute, not on every request.
    with closing(db.connect()) as conn:
        first = conn.execute("SELECT api_key_used_at FROM accounts WHERE id = ?", (me["account_id"],)).fetchone()[0]
        with conn:
            conn.execute("UPDATE accounts SET api_key_used_at = '2000-01-01T00:00:00Z' WHERE id = ?", (me["account_id"],))
        api.get("/accounts/me", headers=new_key)
        later = conn.execute("SELECT api_key_used_at FROM accounts WHERE id = ?", (me["account_id"],)).fetchone()[0]
    assert first and later > "2000-01-01T00:00:00Z", "an old timestamp is refreshed"

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


def check_rate_limits(real_limits):
    """429 with Retry-After past the limit; per IP for sign-up and checkout, per account for payments;
    X-Forwarded-For only counts with GATEWAY_TRUST_PROXY."""
    app_module.LIMITS.clear()
    app_module.LIMITS.update(real_limits)
    app_module._hits.clear()
    bad = {"xpubs": {"BTC": "not-an-xpub"}}
    codes = [api.post("/accounts", json=bad).status_code for _ in range(6)]
    assert codes == [400] * 5 + [429], codes
    r = api.post("/accounts", json=bad)
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 3500 and "too many" in r.text, r.headers
    # A spoofed X-Forwarded-For changes nothing unless the proxy is trusted.
    assert api.post("/accounts", json=bad, headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 429
    app_module.TRUST_PROXY = True
    try:
        # The proxy appends the real IP last; whatever the client claimed before it is ignored.
        h = {"X-Forwarded-For": "1.2.3.4, 203.0.113.9"}
        assert api.post("/accounts", json=bad, headers=h).status_code == 400, "new IP, own budget"
        assert app_module.client_ip(type("R", (), {"headers": {"x-forwarded-for": h["X-Forwarded-For"]}, "client": None})()) == "203.0.113.9"
    finally:
        app_module.TRUST_PROXY = False


def check_sealed(secret):
    """xpubs and webhook secrets are encrypted in the database; old plaintext rows get migrated."""
    with closing(db.connect()) as conn:
        dump = " | ".join(str(tuple(r)) for t in ("accounts", "account_xpubs")
                          for r in conn.execute(f"SELECT * FROM {t}"))
        assert CASES["BTC"][0] not in dump and secret not in dump, "plaintext secret in the database"
        row = conn.execute("SELECT xpub, xpub_tag FROM account_xpubs WHERE xpub_tag = ?",  # this test's own account
                           (db.tag(CASES["BTC"][0]),)).fetchone()
        assert row["xpub"].startswith("v1:") and db.unseal(row["xpub"], "xpub") == CASES["BTC"][0]
        assert row["xpub_tag"] == db.tag(CASES["BTC"][0])
        try:
            db.unseal(row["xpub"], "webhook_secret")
            raise AssertionError("a sealed xpub must not open as another column")
        except Exception as e:
            assert not isinstance(e, AssertionError), e
        # Same xpub again: still refused, found through the tag.
        assert api.post("/accounts", json={"xpubs": {"BTC": CASES["BTC"][0]}}).status_code == 409
        # A database from before encryption: plaintext rows are sealed by db.init(), once.
        with conn:
            conn.execute("INSERT INTO accounts (api_key_hash, created_at, webhook_secret) VALUES ('legacy', 'x', 'whsec_old')")
            legacy = conn.execute("SELECT id FROM accounts WHERE api_key_hash = 'legacy'").fetchone()["id"]
            conn.execute("INSERT INTO account_xpubs (account_id, chain, xpub) VALUES (?, 'DOGE', 'dgub-legacy')", (legacy,))
    db.init()
    db.init()  # idempotent
    with closing(db.connect()) as conn:
        x = conn.execute("SELECT xpub, xpub_tag FROM account_xpubs WHERE account_id = ?", (legacy,)).fetchone()
        w = conn.execute("SELECT webhook_secret FROM accounts WHERE id = ?", (legacy,)).fetchone()["webhook_secret"]
        assert db.unseal(x["xpub"], "xpub") == "dgub-legacy" and x["xpub_tag"] == db.tag("dgub-legacy")
        assert db.unseal(w, "webhook_secret") == "whsec_old"
        with conn:
            conn.execute("DELETE FROM account_xpubs WHERE account_id = ?", (legacy,))
            conn.execute("DELETE FROM accounts WHERE id = ?", (legacy,))


def demo():
    real_limits = dict(app_module.LIMITS)
    app_module.LIMITS.update({k: (10**6, 60) for k in real_limits})  # limits are tested at the end
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
    check_sealed(secret)

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
    check_shop_and_idempotency()
    check_price_requests()
    check_evm_send(key)
    check_token_send(key)

    # --- watcher with a fake chain -----------------------------------------------------------------
    chain = {}  # address -> (seen, confirmed) in smallest units
    down = set()

    def observe(c, asset, address, confirmations):
        if address in down:
            raise ChainError("provider down")
        return chain.get((c, asset, address), (0, 0))

    lookups = []  # (chain, address) per hash lookup
    lookup_down = set()

    def lookup(c, asset, address, since):
        lookups.append((c, address))
        if address in lookup_down:
            raise ChainError("explorer down")
        if c in ("ETH", "BSC") and asset in ("ETH", "BNB"):
            return None  # native ETH/BNB: not searchable
        return ["h" + str(n) + address[-6:] for n in range(1 + min(2, chain.get((c, asset, address), (0, 0))[0] // 10**8))]

    me = api.get("/accounts/me", headers=key).json()["account_id"]  # not assumed to be 1: other test files share the database
    t0 = datetime.now(timezone.utc)
    step = lambda minutes: t0 + timedelta(minutes=minutes)
    with closing(db.connect()) as conn:
        run = lambda when: watcher.tick(conn, now=when, observe=observe, lookup=lookup)

        run(step(1))  # nothing paid yet
        status = lambda p: api.get(f"/payments/{p['id']}", headers=key).json()
        assert status(btc)["status"] == "pending" and not Receiver.calls
        assert not lookups, "no hash lookups while nothing arrived"
        assert status(btc)["transactions"] == [] and status(btc)["address_url"].endswith("/bitcoin/address/" + btc["address"])

        chain[("BTC", "BTC", btc["address"])] = (100_000, 0)       # full amount seen, 0 confirmations
        chain[("TRX", "USDT", trx["address"])] = (5_000_000, 5_000_000)  # half of 10.5 USDT
        down.add(bsc["address"])                                   # BSC provider failing
        run(step(3))
        assert status(btc)["status"] == "confirming" and status(btc)["amount_received"] == "0.001"
        assert status(trx)["status"] == "pending" and status(trx)["amount_received"] == "5"
        tx = status(btc)["transactions"]
        assert tx == [{"hash": "h0" + btc["address"][-6:], "url": "https://blockchair.com/bitcoin/transaction/h0" + btc["address"][-6:]}], tx
        co = api.get(f"/pay/{btc['id']}/status").json()
        assert "transactions" not in co and co["address_url"].endswith(btc["address"]), "checkout: address link, no hashes"
        assert len(Receiver.calls) == 1
        assert json.loads(Receiver.calls[0][1])["payment"]["transactions"] == tx, "webhooks carry the hashes"
        n = len(lookups)
        sig, body = Receiver.calls[0]
        assert webhooks.verify(secret, body, sig), "webhook signature must verify"
        assert json.loads(body)["event"] == "payment.confirming" and json.loads(body)["payment"]["id"] == btc["id"]

        chain[("BTC", "BTC", btc["address"])] = (100_000, 100_000)
        run(step(5))
        assert len(lookups) == n, "confirmations alone don't trigger a lookup (amount unchanged)"
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
        late = payments.public(payments.create(conn, me, "ETH", "ETH", "0.5", now=step(40)))
        chain[("ETH", "ETH", late["address"])] = (5 * 10**17, 0)
        run(step(41))
        assert status(late)["status"] == "confirming"
        assert status(late)["transactions"] == [] and "etherscan.io/address/" in status(late)["address_url"]
        run(step(40 + 30 + 24 * 60 + 1))
        assert status(late)["status"] == "expired"

        # Webhook retries: receiver failing -> retried later, then delivered.
        Receiver.status, before = 500, len(Receiver.calls)
        p = payments.public(payments.create(conn, me, "TRX", "TRX", "1", now=step(2000)))
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

        # --- no address reuse; late funds ---------------------------------------------------------------
        # btc2 expired unpaid at step(30). A day later it still isn't handed out again.
        fresh = payments.create(conn, me, "BTC", "BTC", "1", now=step(30 + 24 * 60 + 1))
        assert fresh["address"] not in (btc["address"], btc2["address"]), "addresses are never reused"

        # Paid after expiry, within 24h: still watched; stays expired, fires payment.paid_late once.
        chain[("BTC", "BTC", btc2["address"])] = (50_000_000, 0)
        lookup_down.add(btc2["address"])  # the hash lookup failing never blocks the amount/status
        run(step(100))
        assert status(btc2)["status"] == "expired" and status(btc2)["amount_received"] == "0.5"
        assert status(btc2)["transactions"] == []
        lookup_down.clear()
        assert json.loads(Receiver.calls[-1][1])["event"] != "payment.paid_late", "not until confirmed"
        chain[("BTC", "BTC", btc2["address"])] = (50_000_000, 50_000_000)
        run(step(120))
        assert json.loads(Receiver.calls[-1][1])["event"] == "payment.paid_late"
        assert status(btc2)["amount_confirmed"] == "0.5"
        n = len(Receiver.calls)
        run(step(140))
        assert len(Receiver.calls) == n, "no repeat paid_late for the same funds"
        # A balance-based chain (EVM/Tron) going down after the merchant sends the coins on: kept.
        chain[("BTC", "BTC", btc2["address"])] = (0, 0)
        run(step(160))
        assert status(btc2)["amount_received"] == "0.5", "expired payments keep what they received"

        # Past the 24h watch: the watcher ignores it; the wallet's scan finds it.
        chain[("BSC", "USDT", bsc["address"])] = (3 * 10**18, 3 * 10**18)
        run(step(32 + 24 * 60 + 30))
        assert status(bsc)["amount_received"] == "0", "watcher stops 24h after expiry"
        real_observe, real_lookup = app_module.chain_client.observe, app_module.chain_client.tx_hashes
        app_module.chain_client.observe = lambda c, a, addr, n: observe(c, a, addr, n)
        app_module.chain_client.tx_hashes = lookup
        down.add(fresh["address"])
        try:
            r = api.post("/wallet/scan", headers=key)
        finally:
            app_module.chain_client.observe, app_module.chain_client.tx_hashes = real_observe, real_lookup
            down.clear()
        # bsc found, fresh unreachable, the rest checked; btc2 (already received) not rescanned.
        assert r.status_code == 200 and r.json()["found"] == 1 and r.json()["failed"] == 1, r.text
        assert status(bsc)["status"] == "expired" and status(bsc)["amount_received"] == "3"
        assert status(bsc)["transactions"][0]["url"].startswith("https://bscscan.com/tx/h")
        ev = conn.execute("SELECT event FROM webhook_events WHERE payment_id = ? ORDER BY id DESC", (bsc["id"],)).fetchone()
        assert ev["event"] == "payment.paid_late", "queued; the watcher delivers it"
        assert api.post("/wallet/scan", headers=other).json()["found"] == 0  # only your own payments

        # Two watchers applying the same transition from the same stale row: one webhook, not two.
        stale = payments.create(conn, me, "BTC", "BTC", "1", now=step(6000))
        for _ in range(2):
            payments.apply_observation(conn, stale, 10**8, 10**8, step(6001))
        n = conn.execute("SELECT COUNT(*) FROM webhook_events WHERE payment_id = ?", (stale["id"],)).fetchone()[0]
        assert n == 1, f"duplicate webhooks for one transition: {n}"

    # Test event: signed like a real one, reported back; deliveries list only the account's own events.
    before = len(Receiver.calls)
    r = api.post("/accounts/me/webhook/test", headers=key)
    assert r.status_code == 200 and r.json() == {"delivered": True, "error": None}, r.text
    sig, body = Receiver.calls[before]
    assert webhooks.verify(secret, body, sig) and json.loads(body)["event"] == "payment.test"
    assert json.loads(body)["payment"]["id"].startswith("pay_test_"), "can't be mistaken for a real payment"
    Receiver.status = 503
    assert api.post("/accounts/me/webhook/test", headers=key).json() == {"delivered": False, "error": "HTTP 503"}
    Receiver.status = 200
    assert api.post("/accounts/me/webhook/test", headers=other).status_code == 400, "no webhook set"
    d = api.get("/accounts/me/webhook/deliveries", headers=key).json()["deliveries"]
    assert d and {x["status"] for x in d} <= {"delivered", "queued", "retrying", "failed"}, d
    assert any(x["event"] == "payment.confirmed" and x["status"] == "delivered" for x in d), d
    assert api.get("/accounts/me/webhook/deliveries", headers=other).json() == {"deliveries": []}

    check_rate_limits(real_limits)
    app_module.LIMITS["payments"] = (2, 60)  # per account: another account has its own budget
    body = {"chain": "BTC", "asset": "BTC", "amount": "1"}
    assert [api.post("/payments", json=body, headers=key).status_code for _ in range(3)] == [201, 201, 429]
    assert api.post("/payments", json={"chain": "DOGE", "asset": "DOGE", "amount": "1"}, headers=other).status_code == 201
    app_module.LIMITS["checkout"] = (1, 60)
    assert [api.get(f"/pay/{btc['id']}/status").status_code for _ in range(2)] == [200, 429]
    app_module.LIMITS.update(real_limits)

    server.shutdown()
    print("payments self-check OK")


def test_selfcheck():
    """pytest entry point; `python test_payments.py` still works."""
    demo()


if __name__ == "__main__":
    demo()
