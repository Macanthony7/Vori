"""Non-custodial payment gateway API. Also serves the onboarding pages from ../frontend/public.

Run from backend/:  python -m uvicorn app:app --port 8000
Payments are watched by a separate process: python watcher.py
Docs: ../docs/api.md
"""
import hashlib
import secrets
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from coincurve import PublicKey
from pydantic import BaseModel, Field

import chain_client
import db
import payments
from assets import ASSETS, EVM_CHAINS, AmountError, from_units
from webhooks import WebhookError, check_url
from xpubs import CHAINS, XpubError, address, normalize, public_key

HERE = Path(__file__).parent
MAIN_INDEX = 0  # each account's main address (Receive, gas for Send). Payments use 1, 2, 3...
PUBLIC = HERE.parent / "frontend" / "public"  # built onboarding pages (see frontend/build_frame.py)
db.init()


def hash_key(api_key):
    # A plain sha256 is enough: keys are 256-bit random, so there is nothing to brute-force.
    return hashlib.sha256(api_key.encode()).hexdigest()


app = FastAPI(title="Payment gateway", docs_url="/api/docs", redoc_url=None)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    # Only our own pages may frame our pages (protects the seed frame and its postMessage).
    response.headers["Content-Security-Policy"] = "frame-ancestors 'self'"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path.startswith(("/accounts", "/payments", "/pay/", "/auth", "/wallet")):
        response.headers["Cache-Control"] = "no-store"  # API keys and payment data must not sit in caches
    return response


def fail(status, message):
    raise HTTPException(status, {"error": message})


# --- Accounts -------------------------------------------------------------------------------------

class NewAccount(BaseModel):
    xpubs: dict[str, str] = Field(min_length=1, max_length=len(CHAINS))


def first_addresses(xpubs):
    out = {chain: {"first_address": address(chain, x, 0)} for chain, x in xpubs.items()}
    if "ETH" in out:
        out["ETH"]["also_used_for"] = ["BSC"]
    return out


@app.post("/accounts", status_code=201)
def create_account(body: NewAccount):
    normalized, errors = {}, {}
    for chain, key in body.xpubs.items():
        chain = chain.upper()
        try:
            normalized[chain] = normalize(chain, key)
        except XpubError as e:
            errors[chain] = str(e)
    if errors:
        raise HTTPException(400, {"errors": errors})

    api_key = "gw_" + secrets.token_urlsafe(32)
    created = datetime.now(timezone.utc).isoformat()
    try:
        with closing(db.connect()) as conn, conn:  # `with conn` = one transaction
            account_id = conn.execute(
                "INSERT INTO accounts (api_key_hash, created_at) VALUES (?, ?)", (hash_key(api_key), created)
            ).lastrowid
            conn.executemany(
                "INSERT INTO account_xpubs (account_id, chain, xpub, next_index) VALUES (?, ?, ?, ?)",
                [(account_id, chain, x, MAIN_INDEX + 1) for chain, x in normalized.items()],
            )
    except sqlite3.IntegrityError:
        # Same xpub already registered. Never reveal or return the existing account.
        fail(409, "one of these xpubs is already registered to an account")

    return {
        "account_id": account_id,
        "api_key": api_key,  # shown once; only its hash is stored
        "plan": "free",
        "chains": first_addresses(normalized),
    }


def account_for(authorization):
    """Bearer API key ("gw_...") or wallet session token ("gws_...", from POST /auth/login)."""
    scheme, _, key = (authorization or "").partition(" ")
    key = key.strip()
    if scheme.lower() != "bearer" or not key:
        fail(401, "missing API key (Authorization: Bearer <key>)")
    with closing(db.connect()) as conn:
        if key.startswith("gws_"):
            row = conn.execute(
                """SELECT a.* FROM sessions s JOIN accounts a ON a.id = s.account_id
                   WHERE s.token_hash = ? AND s.expires_at > ?""", (hash_key(key), db.now())).fetchone()
        else:
            row = conn.execute("SELECT * FROM accounts WHERE api_key_hash = ?", (hash_key(key),)).fetchone()
    if not row:
        fail(401, "invalid or expired API key / session")
    return row


@app.get("/accounts/me")
def me(authorization: str = Header(None)):
    acct = account_for(authorization)
    with closing(db.connect()) as conn:
        rows = conn.execute("SELECT chain, xpub FROM account_xpubs WHERE account_id = ?", (acct["id"],)).fetchall()
    return {
        "account_id": acct["id"],
        "plan": acct["plan"],
        "plan_expires_at": acct["plan_expires_at"],
        "webhook_url": acct["webhook_url"],
        "chains": first_addresses({r["chain"]: r["xpub"] for r in rows}),
    }


class WebhookSettings(BaseModel):
    url: str


@app.put("/accounts/me/webhook")
def set_webhook(body: WebhookSettings, authorization: str = Header(None)):
    acct = account_for(authorization)
    try:
        url = check_url(body.url)
    except WebhookError as e:
        fail(400, str(e))
    secret = "whsec_" + secrets.token_urlsafe(32)  # new secret on every change
    with closing(db.connect()) as conn, conn:
        conn.execute("UPDATE accounts SET webhook_url = ?, webhook_secret = ? WHERE id = ?", (url, secret, acct["id"]))
    return {"url": url, "secret": secret}


@app.delete("/accounts/me/webhook", status_code=204)
def delete_webhook(authorization: str = Header(None)):
    acct = account_for(authorization)
    with closing(db.connect()) as conn, conn:
        conn.execute("UPDATE accounts SET webhook_url = NULL, webhook_secret = NULL WHERE id = ?", (acct["id"],))
    return Response(status_code=204)


@app.post("/accounts/me/api-key")
def rotate_api_key(authorization: str = Header(None)):
    """New API key; the old one stops working. With a wallet session this is key recovery."""
    acct = account_for(authorization)
    api_key = "gw_" + secrets.token_urlsafe(32)
    with closing(db.connect()) as conn, conn:
        conn.execute("UPDATE accounts SET api_key_hash = ? WHERE id = ?", (hash_key(api_key), acct["id"]))
    return {"api_key": api_key}


# --- Wallet login: sign a challenge with the key at m/44'/0'/account'/0/0 -----------------------------
# The wallet frame signs sha256(LOGIN_PREFIX + challenge). The fixed text prefix means a login
# signature can never double as a signature over a transaction hash.
LOGIN_PREFIX = "Gateway login\n"
CHALLENGE_TTL = timedelta(minutes=5)
SESSION_TTL = timedelta(hours=12)


@app.post("/auth/challenge")
def login_challenge():
    challenge = "gw-login:" + secrets.token_urlsafe(24)
    now = datetime.now(timezone.utc)
    with closing(db.connect()) as conn, conn:
        conn.execute("DELETE FROM login_challenges WHERE expires_at < ?", (db.at(now),))
        conn.execute("INSERT INTO login_challenges (nonce, expires_at) VALUES (?, ?)",
                     (challenge, db.at(now + CHALLENGE_TTL)))
    return {"challenge": challenge, "expires_at": db.at(now + CHALLENGE_TTL)}


class Login(BaseModel):
    xpub: str = Field(max_length=200)        # the wallet's BTC account xpub (public)
    challenge: str = Field(max_length=100)
    signature: str = Field(max_length=200)   # hex DER ECDSA signature


@app.post("/auth/login")
def login(body: Login):
    now = datetime.now(timezone.utc)
    with closing(db.connect()) as conn, conn:
        # Single use: the challenge is gone whether or not the signature checks out.
        fresh = conn.execute("DELETE FROM login_challenges WHERE nonce = ? AND expires_at > ?",
                             (body.challenge, db.at(now))).rowcount
    if not fresh:
        fail(401, "challenge expired or already used; request a new one")
    try:
        xpub = normalize("BTC", body.xpub)
        signature = bytes.fromhex(body.signature)
    except (XpubError, ValueError):
        fail(401, "invalid login")
    with closing(db.connect()) as conn:
        row = conn.execute("SELECT account_id FROM account_xpubs WHERE chain = 'BTC' AND xpub = ?", (xpub,)).fetchone()
    if not row:
        fail(401, "no account for this wallet")
    try:
        ok = PublicKey(public_key("BTC", xpub, MAIN_INDEX)).verify(signature, (LOGIN_PREFIX + body.challenge).encode())
    except (ValueError, TypeError):
        ok = False
    if not ok:
        fail(401, "invalid signature")
    token = "gws_" + secrets.token_urlsafe(32)
    with closing(db.connect()) as conn, conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (db.at(now),))
        conn.execute("INSERT INTO sessions (token_hash, account_id, expires_at) VALUES (?, ?, ?)",
                     (hash_key(token), row["account_id"], db.at(now + SESSION_TTL)))
    return {"token": token, "account_id": row["account_id"], "expires_at": db.at(now + SESSION_TTL)}


@app.delete("/auth/session", status_code=204)
def logout(authorization: str = Header(None)):
    key = (authorization or "").partition(" ")[2].strip()
    with closing(db.connect()) as conn, conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (hash_key(key),))
    return Response(status_code=204)


# --- Wallet balances ----------------------------------------------------------------------------
# ponytail: per-process memory cache; fine with one worker (required while on SQLite anyway).
BALANCE_TTL = 60  # seconds
_balance_cache = {}  # (chain, asset, address) -> (monotonic time, units)


def cached_balance(job):
    hit = _balance_cache.get(job)
    if hit and time.monotonic() - hit[0] < BALANCE_TTL:
        return hit[1]
    try:
        units = chain_client.balance(*job)
    except chain_client.ChainError:
        return None  # unknown, not zero; failures aren't cached
    _balance_cache[job] = (time.monotonic(), units)
    return units


@app.get("/wallet/balances")
def wallet_balances(authorization: str = Header(None)):
    """Live balance per asset: the main address plus every payment address that ever received funds."""
    acct = account_for(authorization)
    with closing(db.connect()) as conn:
        xpubs = {r["chain"]: r["xpub"] for r in conn.execute(
            "SELECT chain, xpub FROM account_xpubs WHERE account_id = ?", (acct["id"],))}
        funded = conn.execute(
            """SELECT DISTINCT chain, asset, address, address_index FROM payments
               WHERE account_id = ? AND received != '0'""", (acct["id"],)).fetchall()

    targets = {}  # (chain, asset) -> {address: index}
    for pair, spec in ASSETS.items():
        if spec["xpub"] in xpubs:
            targets[pair] = {address(spec["xpub"], xpubs[spec["xpub"]], MAIN_INDEX): MAIN_INDEX}
    for r in funded:
        targets.setdefault((r["chain"], r["asset"]), {})[r["address"]] = r["address_index"]

    jobs = [(chain, asset, addr) for (chain, asset), addrs in targets.items() for addr in addrs]
    with ThreadPoolExecutor(max_workers=8) as pool:
        found = dict(zip(jobs, pool.map(cached_balance, jobs)))

    out = []
    for (chain, asset), addrs in targets.items():
        d = ASSETS[(chain, asset)]["decimals"]
        values = [(addr, index, found[(chain, asset, addr)]) for addr, index in addrs.items()]
        out.append({
            "chain": chain,
            "asset": asset,
            "balance": from_units(sum(v for _, _, v in values if v), d),
            "complete": all(v is not None for _, _, v in values),  # false = a provider couldn't be reached
            "main_address": next(a for a, i in addrs.items() if i == MAIN_INDEX),
            "addresses": [{"address": a, "index": i, "balance": from_units(v, d)} for a, i, v in values if v],
        })
    return {"balances": out}


# --- Wallet send (BTC / LTC / DOGE, and native ETH / BNB / Sepolia ETH) ---------------------------
# The server only reads balances/coins and relays signed transactions. Planning, the confirm screen
# and signing happen in the offline frame, which also re-derives every listed address before signing.
UTXO_CHAINS = ("BTC", "LTC", "DOGE")
EVM_GAS_LIMIT = 21000  # a plain native transfer


def wallet_addresses(account_id, xpub_chain, xpub):
    """{address: index}: the main address plus every payment address that ever received funds."""
    with closing(db.connect()) as conn:
        rows = conn.execute(
            """SELECT DISTINCT address, address_index FROM payments
               WHERE account_id = ? AND xpub_chain = ? AND received != '0'""", (account_id, xpub_chain)).fetchall()
    out = {address(xpub_chain, xpub, MAIN_INDEX): MAIN_INDEX}
    out.update({r["address"]: r["address_index"] for r in rows})
    return out


def sendable(chain):
    if chain in UTXO_CHAINS:
        return "utxo"
    evm = EVM_CHAINS.get(chain)
    if evm and (chain, evm["native"]) in ASSETS:  # Sepolia only with GATEWAY_TESTNETS=1
        return "evm"
    fail(400, f"sending {chain} isn't supported yet")


@app.get("/wallet/send/{chain}")
def send_prepare(chain: str, authorization: str = Header(None)):
    acct = account_for(authorization)
    chain = chain.upper()
    kind = sendable(chain)
    xpub_chain = chain if kind == "utxo" else "ETH"
    with closing(db.connect()) as conn:
        row = conn.execute("SELECT xpub FROM account_xpubs WHERE account_id = ? AND chain = ?",
                           (acct["id"], xpub_chain)).fetchone()
    if not row:
        fail(400, f"no {xpub_chain} xpub is registered on this account")
    addrs = wallet_addresses(acct["id"], xpub_chain, row["xpub"])
    try:
        if kind == "utxo":
            with ThreadPoolExecutor(max_workers=3) as pool:  # BlockCypher allows ~3 requests/s without a token
                found = list(pool.map(lambda a: chain_client.unspent(chain, a), addrs))
            fees = chain_client.fee_rates(chain)
        else:
            with ThreadPoolExecutor(max_workers=8) as pool:
                accounts = list(pool.map(lambda a: chain_client.evm_account(chain, a), addrs))
            fees = chain_client.evm_fees(chain)
    except chain_client.ChainError as e:
        fail(502, f"couldn't read the {chain} network right now ({e}); try again in a minute")
    if kind == "utxo":
        utxos = [{"address": a, "index": addrs[a], **u} for a, us in zip(addrs, found) for u in us]
        return {"chain": chain, "kind": kind, "utxos": utxos, "fee_per_byte": fees, "change_index": MAIN_INDEX,
                "change_address": address(chain, row["xpub"], MAIN_INDEX)}
    # Each address with a balance sends its own transaction and pays its own fee.
    sources = [{"address": a, "index": addrs[a], "balance": str(bal), "nonce": nonce}
               for a, (bal, nonce) in zip(addrs, accounts) if bal > 0]
    return {"chain": chain, "kind": kind, "asset": EVM_CHAINS[chain]["native"], "chain_id": EVM_CHAINS[chain]["chain_id"],
            "gas_limit": EVM_GAS_LIMIT, "fees": fees, "sources": sources}


class Broadcast(BaseModel):
    txs: list[str] = Field(min_length=1, max_length=50)  # signed transactions, hex


@app.post("/wallet/send/{chain}")
def send_broadcast(chain: str, body: Broadcast, authorization: str = Header(None)):
    account_for(authorization)  # ponytail: any signed tx is relayed; signing is what protects funds
    chain = chain.upper()
    kind = sendable(chain)
    txs = [t.strip().removeprefix("0x") for t in body.txs]
    if any(len(t) < 20 or len(t) > 200_000 or any(c not in "0123456789abcdefABCDEF" for c in t) for t in txs):
        fail(422, "each transaction must be hex")
    relay = chain_client.broadcast if kind == "utxo" else chain_client.evm_broadcast
    hashes, error = [], None
    for tx in txs:  # in order; stop at the first rejection and report what went through
        try:
            hashes.append(relay(chain, tx))
        except chain_client.ChainError as e:
            error = f"the {chain} network didn't accept transaction {len(hashes) + 1} of {len(txs)}: {e}"
            break
    for key in [k for k in _balance_cache if k[0] == chain]:
        _balance_cache.pop(key, None)  # balances changed
    if error and not hashes:
        fail(502, error)
    return {"chain": chain, "tx_hashes": hashes, "error": error}


# --- Payments -------------------------------------------------------------------------------------

class NewPayment(BaseModel):
    chain: str
    asset: str
    amount: str
    reference: Optional[str] = None


@app.post("/payments", status_code=201)
def create_payment(body: NewPayment, authorization: str = Header(None)):
    acct = account_for(authorization)
    with closing(db.connect()) as conn:
        try:
            p = payments.create(conn, acct["id"], body.chain, body.asset, body.amount, body.reference)
        except (AmountError, payments.PaymentError) as e:
            fail(400, str(e))
    return payments.public(p)


@app.get("/payments/{payment_id}")
def get_payment(payment_id: str, authorization: str = Header(None)):
    acct = account_for(authorization)
    with closing(db.connect()) as conn:
        p = payments.get(conn, acct["id"], payment_id)
    if not p:
        fail(404, "payment not found")
    return payments.public(p)


@app.get("/payments")
def list_payments(status: Optional[str] = None, limit: int = 50, authorization: str = Header(None)):
    acct = account_for(authorization)
    limit = max(1, min(limit, 100))
    query, params = "SELECT * FROM payments WHERE account_id = ?", [acct["id"]]
    if status:
        query, params = query + " AND status = ?", params + [status]
    with closing(db.connect()) as conn:
        rows = conn.execute(query + " ORDER BY created_at DESC, rowid DESC LIMIT ?", (*params, limit)).fetchall()
    return {"payments": [payments.public(p) for p in rows]}


# --- Checkout (public, for the payer) ---------------------------------------------------------------

@app.get("/pay/{payment_id}/status")
def checkout_status(payment_id: str):
    # ponytail: no rate limit yet (roadmap item 7); ids are 128-bit random, so guessing one is hopeless.
    with closing(db.connect()) as conn:
        p = conn.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
    if not p:
        fail(404, "payment not found")
    return payments.checkout(p)


@app.get("/pay/{payment_id}", include_in_schema=False)
def checkout_page(payment_id: str):
    return FileResponse(PUBLIC / "pay.html")  # the page loads its data from /pay/{id}/status


# Frontend (onboarding pages). Mounted last so API routes win.
app.mount("/", StaticFiles(directory=PUBLIC, html=True), name="static")
