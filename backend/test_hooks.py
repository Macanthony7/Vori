"""BlockCypher push hooks and response compression.

Hooks: created by the watcher for open BTC/LTC/DOGE payments, removed when they're final; the callback only
asks for an earlier check (never trusted as proof); polling slows while a hook is active and stays normal
when none could be made. Compression: the signing engine is served gzipped and its SHA-256 pins still hold.
"""
import hashlib
import os
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from bip_utils import Bip44, Bip44Coins
from fastapi.testclient import TestClient

import app as app_module
import chain_client
import db
import payments
import watcher

api = TestClient(app_module.app)
ENGINE = Path(__file__).parent.parent / "frontend" / "public" / "vendor" / "wallet-core"


@pytest.fixture
def account():
    db.init()
    seed = os.urandom(64)
    xpub = lambda coin: Bip44.FromSeed(seed, coin).Purpose().Coin().Account(0).PublicKey().ToExtended()
    r = api.post("/accounts", json={"xpubs": {"BTC": xpub(Bip44Coins.BITCOIN), "ETH": xpub(Bip44Coins.ETHEREUM)}})
    assert r.status_code == 201, r.text
    return {"Authorization": "Bearer " + r.json()["api_key"]}


@pytest.fixture
def hooks(monkeypatch):
    made, removed, fail = [], [], {"create": False, "delete": False}

    def create(chain, address, url, confirmations):
        if fail["create"]:
            raise chain_client.ChainError("limit reached")
        made.append((chain, address, url, confirmations))
        return f"hook-{len(made)}"

    def delete(chain, hook_id):
        if fail["delete"]:
            raise chain_client.ChainError("HTTP 500")
        removed.append(hook_id)
    monkeypatch.setattr(watcher, "HOOKS", True)
    monkeypatch.setattr(chain_client, "create_hook", create)
    monkeypatch.setattr(chain_client, "delete_hook", delete)
    return {"made": made, "removed": removed, "fail": fail}


def row(pid):
    with closing(db.connect()) as conn:
        return conn.execute("SELECT * FROM payments WHERE id = ?", (pid,)).fetchone()


def test_watcher_creates_hooks_only_for_utxo_payments_and_removes_them(account, hooks):
    btc = api.post("/payments", json={"chain": "BTC", "asset": "BTC", "amount": "0.01"}, headers=account).json()
    eth = api.post("/payments", json={"chain": "ETH", "asset": "ETH", "amount": "0.1"}, headers=account).json()
    now = datetime.now(timezone.utc)
    with closing(db.connect()) as conn:
        watcher.manage_hooks(conn, now)
        watcher.manage_hooks(conn, now)  # nothing twice
    mine = [h for h in hooks["made"] if h[1] == btc["address"]]
    assert len(mine) == 1 and mine[0][0] == "BTC" and mine[0][3] == 2, hooks["made"]
    assert mine[0][2] == payments.hook_url(btc["id"]) and "?t=" in mine[0][2]
    assert not [h for h in hooks["made"] if h[1] == eth["address"]], "EVM payments are never hooked"
    assert row(btc["id"])["bc_hook"].startswith("hook-") and row(eth["id"])["bc_hook"] is None

    # While hooked, polling is the 10-minute fallback.
    with closing(db.connect()) as conn:
        payments.apply_observation(conn, row(btc["id"]), 0, 0, now)
    assert row(btc["id"])["next_check_at"] == db.at(now + payments.HOOKED_CHECK)

    # Paid: the hook is removed (retried while BlockCypher fails).
    with closing(db.connect()) as conn:
        payments.apply_observation(conn, row(btc["id"]), 10**6, 10**6, now)
        hooks["fail"]["delete"] = True
        watcher.manage_hooks(conn, now)
        assert row(btc["id"])["bc_hook"].startswith("hook-"), "kept until the delete works"
        hooks["fail"]["delete"] = False
        watcher.manage_hooks(conn, now)
    assert row(btc["id"])["bc_hook"] == "done" and hooks["removed"]


def test_no_hook_means_normal_polling(account, hooks):
    hooks["fail"]["create"] = True
    p = api.post("/payments", json={"chain": "BTC", "asset": "BTC", "amount": "0.01"}, headers=account).json()
    now = datetime.now(timezone.utc)
    with closing(db.connect()) as conn:
        watcher.manage_hooks(conn, now)
        assert row(p["id"])["bc_hook"] == "none"
        payments.apply_observation(conn, row(p["id"]), 0, 0, now)
    assert row(p["id"])["next_check_at"] == db.at(now + timedelta(seconds=payments.CHECK_EVERY["BTC"]))


def test_hooks_off_without_token_or_https(monkeypatch, account):
    monkeypatch.setattr(watcher, "HOOKS", False)
    called = []
    monkeypatch.setattr(chain_client, "create_hook", lambda *a: called.append(a))
    api.post("/payments", json={"chain": "BTC", "asset": "BTC", "amount": "0.01"}, headers=account)
    with closing(db.connect()) as conn:
        watcher.manage_hooks(conn, datetime.now(timezone.utc))
    assert not called
    # The default: on only with a token and a public https address.
    assert watcher.HOOKS is False


def test_callback_only_asks_for_an_early_check(account):
    p = api.post("/payments", json={"chain": "BTC", "asset": "BTC", "amount": "0.01"}, headers=account).json()
    later = db.at(datetime.now(timezone.utc) + timedelta(hours=1))
    with closing(db.connect()) as conn, conn:
        conn.execute("UPDATE payments SET next_check_at = ? WHERE id = ?", (later, p["id"]))
    token = payments.hook_token(p["id"])
    forged = {"total": 10**8, "outputs": [{"addresses": [p["address"]], "value": 10**8}], "confirmations": 6}
    assert api.post(f"/hooks/blockcypher/{p['id']}?t={'0' * 32}", json=forged).status_code == 404
    assert api.post(f"/hooks/blockcypher/{p['id']}", json=forged).status_code == 404
    assert api.post(f"/hooks/blockcypher/pay_unknown?t={token}").status_code == 404, "a token is per payment"
    assert row(p["id"])["next_check_at"] == later
    r = api.post(f"/hooks/blockcypher/{p['id']}?t={token}", json=forged)
    assert r.status_code == 204
    after = row(p["id"])
    assert after["next_check_at"] < later, "checked on the next pass"
    assert after["status"] == "pending" and after["received"] == "0", "the body is never trusted as payment"


def test_create_and_delete_hook_requests(monkeypatch):
    sent = []

    class Reply:
        def __init__(self, status, data=None):
            self.status_code, self._data = status, data

        def json(self):
            return self._data
    monkeypatch.setattr(chain_client, "BLOCKCYPHER_TOKEN", "tok")
    monkeypatch.setattr(chain_client.requests, "post", lambda url, **kw: sent.append((url, kw)) or Reply(201, {"id": "abc-123"}))
    assert chain_client.create_hook("LTC", "Laddr", "https://pay.example/hooks/blockcypher/pay_1?t=x", 12) == "abc-123"
    url, kw = sent[0]
    assert url == "https://api.blockcypher.com/v1/ltc/main/hooks" and kw["params"] == {"token": "tok"}
    assert kw["json"] == {"event": "tx-confirmation", "address": "Laddr",
                          "url": "https://pay.example/hooks/blockcypher/pay_1?t=x", "confirmations": 10}, "max 10"
    monkeypatch.setattr(chain_client.requests, "post", lambda url, **kw: Reply(429, {"error": "Limits reached"}))
    with pytest.raises(chain_client.ChainError, match="Limits reached"):
        chain_client.create_hook("BTC", "1a", "https://x", 2)
    for status, ok in ((204, True), (404, True), (500, False)):
        monkeypatch.setattr(chain_client.requests, "delete", lambda url, **kw: Reply(status))
        if ok:
            chain_client.delete_hook("BTC", "abc")
        else:
            with pytest.raises(chain_client.ChainError):
                chain_client.delete_hook("BTC", "abc")


def test_signing_engine_is_served_gzipped_and_still_matches_its_pins():
    sums = {}
    for line in (ENGINE / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        sums[name.lstrip("*")] = digest
    for name in ("core_proto.js", "wallet-core.wasm"):
        url = f"/vendor/wallet-core/{name}"
        with api.stream("GET", url, headers={"Accept-Encoding": "gzip"}) as r:
            assert r.status_code == 200 and r.headers["content-encoding"] == "gzip", name
            sent = sum(len(chunk) for chunk in r.iter_raw())  # bytes on the wire, still compressed
        assert sent < (ENGINE / name).stat().st_size * 0.5, (name, sent)
        body = api.get(url, headers={"Accept-Encoding": "gzip"}).content  # decompressed, as the frame sees it
        assert hashlib.sha256(body).hexdigest() == sums[name], name
