"""Non-custodial payment gateway API. Also serves the onboarding pages from ../frontend/public.

Run from backend/:  python -m uvicorn app:app --port 8000
Payments are watched by a separate process: python watcher.py
Docs: ../docs/api.md
"""
import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from bip_utils import Base58Decoder
from coincurve import PublicKey
from pydantic import BaseModel, Field

import chain_client
import db
import payments
import rates
from assets import ASSETS, EVM_CHAINS, TRON_CHAINS, AmountError, from_units
import webhooks
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
# Compress text and the signing engine on the way out: the first Send loads ~21 MB (core_proto.js 10.8 MB,
# wallet-core.wasm 10.4 MB), ~4.7 MB gzipped. The frame checks SHA-256 of the decompressed bytes, so the pins
# still hold. Behind Caddy, its `encode gzip` does the same (deploy/Caddyfile).
# ponytail: compressed per request; serve pre-compressed files if CPU ever matters.
app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=6)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    # Only our own pages may frame our pages (protects the seed frame and its postMessage).
    response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'self'")  # unless a route set a stricter one
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path.startswith(("/accounts", "/payments", "/pay/", "/auth", "/wallet")):
        response.headers["Cache-Control"] = "no-store"  # API keys and payment data must not sit in caches
    return response


def fail(status, message):
    raise HTTPException(status, {"error": message})


# --- Rate limits ------------------------------------------------------------------------------------
# Sliding window per (bucket, key). Docs: docs/api.md#rate-limits
# ponytail: in memory, one server process. Several processes or servers: limit at the reverse proxy
# (nginx limit_req) or move this to Redis.
LIMITS = {                       # bucket: (requests, per seconds)
    "signup": (5, 3600),         # POST /accounts, per IP
    "auth": (20, 60),            # POST /auth/challenge and /auth/login, per IP
    "payments": (60, 60),        # POST /payments, per account
    "checkout": (120, 60),       # GET /pay/{id}/status, per IP (the page polls every 5 s)
    "wallet": (30, 60),          # balances and send prepare/relay, per account
    "wallet_tx": (60, 60),       # GET /wallet/tx: the page polls every 3 s while top-ups land
    "scan": (1, 60),             # POST /wallet/scan, per account
    "webhook_test": (6, 60),     # POST /accounts/me/webhook/test, per account (calls the shop's server)
    "hook": (240, 60),           # BlockCypher callbacks, per IP (it retries a few times per event)
    "market": (60, 60),          # GET /market and its chart history, per IP (cached server-side)
}
# Behind a reverse proxy every request comes from the proxy's IP. Set GATEWAY_TRUST_PROXY=1 only when
# a proxy you run sets X-Forwarded-For; otherwise visitors could pick their own "IP".
TRUST_PROXY = os.getenv("GATEWAY_TRUST_PROXY") == "1"
_hits = {}
_hits_lock = threading.Lock()


def client_ip(request):
    if TRUST_PROXY:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[-1].strip()  # the entry our proxy added (earlier ones are the client's claim)
    return request.client.host if request.client else "unknown"


def rate_limit(bucket, key):
    count, per = LIMITS[bucket]
    now = time.monotonic()
    with _hits_lock:
        if len(_hits) > 50_000:  # forget idle keys so memory stays bounded
            for k in [k for k, q in _hits.items() if not q or q[-1] <= now - 3600]:
                del _hits[k]
        q = _hits.setdefault((bucket, key), deque())
        while q and q[0] <= now - per:
            q.popleft()
        if len(q) >= count:
            retry = int(q[0] + per - now) + 1
            raise HTTPException(429, {"error": f"too many requests; try again in {retry} s"},
                                headers={"Retry-After": str(retry)})
        q.append(now)


# --- Accounts -------------------------------------------------------------------------------------

class NewAccount(BaseModel):
    xpubs: dict[str, str] = Field(min_length=1, max_length=len(CHAINS))


def first_addresses(xpubs):
    out = {chain: {"first_address": address(chain, x, 0)} for chain, x in xpubs.items()}
    if "ETH" in out:
        out["ETH"]["also_used_for"] = ["BSC"]
    return out


@app.post("/accounts", status_code=201)
def create_account(body: NewAccount, request: Request):
    rate_limit("signup", client_ip(request))
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
                "INSERT INTO accounts (api_key_hash, api_key_hint, created_at) VALUES (?, ?, ?)",
                (hash_key(api_key), key_hint(api_key), created)
            ).lastrowid
            conn.executemany(
                "INSERT INTO account_xpubs (account_id, chain, xpub, xpub_tag, next_index) VALUES (?, ?, ?, ?, ?)",
                [(account_id, chain, db.seal(x, "xpub"), db.tag(x), MAIN_INDEX + 1) for chain, x in normalized.items()],
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


def key_hint(api_key):
    """First 7 characters ("gw_" + 4): enough to tell keys apart, far too little to guess one (256 bits)."""
    return api_key[:7]


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
            now = datetime.now(timezone.utc)
            if row and (row["api_key_used_at"] or "") < db.at(now - timedelta(minutes=1)):
                with conn:  # "last used", without a database write on every request
                    conn.execute("UPDATE accounts SET api_key_used_at = ? WHERE id = ?", (db.at(now), row["id"]))
    if not row:
        fail(401, "invalid or expired API key / session")
    return row


def account_for_settings(authorization):
    """For changes a leaked API key must not make: the webhook and the API key itself.

    Once an account has signed in to the wallet, these need a wallet session (a signature by the
    wallet's key), so a stolen API key can only create and read payments. Accounts that never signed in
    (xpub-only, no phrase in our wallet) can still use the API key. Docs: docs/security.md#api-keys
    """
    acct = account_for(authorization)
    if acct["wallet_login_at"] and not (authorization or "").split()[-1].startswith("gws_"):
        fail(403, "this needs the wallet sign-in: open your wallet to change the webhook or the API key")
    return acct


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
        "api_key_hint": acct["api_key_hint"],
        "api_key_used_at": acct["api_key_used_at"],
        "settings_need_wallet": bool(acct["wallet_login_at"]),
        "shop_name": acct["shop_name"],
        "has_logo": bool(acct["shop_logo"]),
        "logo": f"data:{acct['shop_logo_type']};base64,{acct['shop_logo']}" if acct["shop_logo"] else None,
        "chains": first_addresses({r["chain"]: db.unseal(r["xpub"], "xpub") for r in rows}),
    }


class WebhookSettings(BaseModel):
    url: str


@app.put("/accounts/me/webhook")
def set_webhook(body: WebhookSettings, authorization: str = Header(None)):
    acct = account_for_settings(authorization)
    try:
        url = check_url(body.url)
    except WebhookError as e:
        fail(400, str(e))
    secret = "whsec_" + secrets.token_urlsafe(32)  # new secret on every change
    with closing(db.connect()) as conn, conn:
        conn.execute("UPDATE accounts SET webhook_url = ?, webhook_secret = ? WHERE id = ?", (url, db.seal(secret, "webhook_secret"), acct["id"]))
    return {"url": url, "secret": secret}


@app.delete("/accounts/me/webhook", status_code=204)
def delete_webhook(authorization: str = Header(None)):
    acct = account_for_settings(authorization)
    with closing(db.connect()) as conn, conn:
        conn.execute("UPDATE accounts SET webhook_url = NULL, webhook_secret = NULL WHERE id = ?", (acct["id"],))
    return Response(status_code=204)


# --- Shop name and logo (checkout page) -----------------------------------------------------------
LOGO_MAX = 100_000  # bytes
LOGO_TYPES = {"image/png": b"\x89PNG\r\n\x1a\n", "image/jpeg": b"\xff\xd8\xff"}  # type -> file signature


class ShopSettings(BaseModel):
    name: Optional[str] = None    # null or "" clears it
    logo: Optional[str] = None    # data:image/png;base64,... ; null or "" removes it; leave out to keep it


@app.put("/accounts/me/shop")
def set_shop(body: ShopSettings, authorization: str = Header(None)):
    """What payers see on the checkout page. Same protection as the webhook: a leaked API key mustn't be
    able to rename the shop payers think they're paying."""
    acct = account_for_settings(authorization)
    name = (body.name or "").strip() or None
    if name and (len(name) > 60 or any(ord(c) < 32 for c in name)):
        fail(400, "shop name must be at most 60 characters, on one line")
    updates = {"shop_name": name}
    if "logo" in body.model_fields_set:
        updates.update(shop_logo=None, shop_logo_type=None)
        if body.logo:
            head, _, data = body.logo.partition(",")
            kind = head.removeprefix("data:").removesuffix(";base64")
            if kind not in LOGO_TYPES or not head.endswith(";base64"):
                fail(400, "logo must be a PNG or JPEG image")
            try:
                raw = base64.b64decode(data, validate=True)
            except (binascii.Error, ValueError):
                fail(400, "logo isn't valid base64")
            if len(raw) > LOGO_MAX:
                fail(400, f"logo must be at most {LOGO_MAX // 1000} KB")
            if not raw.startswith(LOGO_TYPES[kind]):
                fail(400, "logo isn't a real PNG or JPEG file")
            updates.update(shop_logo=base64.b64encode(raw).decode(), shop_logo_type=kind)
    with closing(db.connect()) as conn, conn:
        conn.execute("UPDATE accounts SET " + ", ".join(f"{k} = ?" for k in updates) + " WHERE id = ?",
                     (*updates.values(), acct["id"]))
    return {"shop_name": name, "has_logo": bool(updates.get("shop_logo", acct["shop_logo"]))}


# What a test event carries: the shape of a real payment, clearly not one ("pay_test_", reference).
SAMPLE_PAYMENT = {
    "id": "pay_test_0000000000000000000000", "status": "confirmed", "chain": "BTC", "asset": "BTC",
    "address": "1BoatSLRHtKNngkdXEeobR76b53LETtpyT", "amount": "0.001", "amount_received": "0.001",
    "amount_confirmed": "0.001", "confirmations_required": 2, "token_contract": None,
    "reference": "test-event", "created_at": "2026-01-01T00:00:00Z", "expires_at": "2026-01-01T00:30:00Z",
    "confirmed_at": "2026-01-01T00:12:00Z", "transactions": [], "address_url": "",
    "checkout_url": "",
}


@app.post("/accounts/me/webhook/test")
def webhook_test(authorization: str = Header(None)):
    """Send a signed `payment.test` event to the webhook now and report how the shop answered."""
    acct = account_for_settings(authorization)
    rate_limit("webhook_test", acct["id"])
    if not acct["webhook_url"]:
        fail(400, "set a webhook URL first")
    body = json.dumps({"event": "payment.test", "payment": SAMPLE_PAYMENT}, separators=(",", ":"), sort_keys=True)
    error = webhooks.send(acct["webhook_url"], db.unseal(acct["webhook_secret"], "webhook_secret"), body)
    return {"delivered": error is None, "error": error}


@app.get("/accounts/me/webhook/deliveries")
def webhook_deliveries(authorization: str = Header(None)):
    """The account's 20 most recent webhook events and how their delivery went."""
    acct = account_for(authorization)
    with closing(db.connect()) as conn:
        rows = conn.execute(
            """SELECT id, payment_id, event, attempts, delivered_at, next_attempt_at, last_error FROM webhook_events
               WHERE account_id = ? ORDER BY id DESC LIMIT 20""", (acct["id"],)).fetchall()
    def status(r):
        if r["delivered_at"]:
            return "delivered"
        if r["next_attempt_at"]:
            return "retrying" if r["attempts"] else "queued"
        return "failed"  # gave up after the last retry, or the webhook was removed
    return {"deliveries": [{"id": r["id"], "payment_id": r["payment_id"], "event": r["event"], "status": status(r),
                            "attempts": r["attempts"], "delivered_at": r["delivered_at"],
                            "next_attempt_at": r["next_attempt_at"], "last_error": r["last_error"]} for r in rows]}


@app.post("/accounts/me/api-key")
def rotate_api_key(authorization: str = Header(None)):
    """New API key; the old one stops working. With a wallet session this is key recovery."""
    acct = account_for_settings(authorization)
    api_key = "gw_" + secrets.token_urlsafe(32)
    with closing(db.connect()) as conn, conn:
        conn.execute("UPDATE accounts SET api_key_hash = ?, api_key_hint = ?, api_key_used_at = NULL WHERE id = ?",
                     (hash_key(api_key), key_hint(api_key), acct["id"]))
    return {"api_key": api_key}


# --- Wallet login: sign a challenge with the key at m/44'/0'/account'/0/0 -----------------------------
# The wallet frame signs sha256(LOGIN_PREFIX + challenge). The fixed text prefix means a login
# signature can never double as a signature over a transaction hash.
LOGIN_PREFIX = "Gateway login\n"
CHALLENGE_TTL = timedelta(minutes=5)
SESSION_TTL = timedelta(hours=12)


@app.post("/auth/challenge")
def login_challenge(request: Request):
    rate_limit("auth", client_ip(request))
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
def login(body: Login, request: Request):
    rate_limit("auth", client_ip(request))
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
        row = conn.execute("SELECT account_id FROM account_xpubs WHERE chain = 'BTC' AND xpub_tag = ?", (db.tag(xpub),)).fetchone()
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
        conn.execute("UPDATE accounts SET wallet_login_at = ? WHERE id = ? AND wallet_login_at IS NULL",
                     (db.at(now), row["account_id"]))
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
    rate_limit("wallet", acct["id"])
    with closing(db.connect()) as conn:
        xpubs = {r["chain"]: db.unseal(r["xpub"], "xpub") for r in conn.execute(
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


@app.post("/wallet/scan")
def wallet_scan(authorization: str = Header(None)):
    """Check every expired payment address that never received anything, and record coins found there.

    The watcher stops looking at a payment 24 h after it expires. This covers anything later.
    Found coins count as received (so balances and Send include them) and fire `payment.paid_late`.
    ponytail: one provider call per address, on demand; batch or page it if accounts get big.
    """
    acct = account_for(authorization)
    rate_limit("scan", acct["id"])
    with closing(db.connect()) as conn:
        # Latest payment per address (older databases may have reused addresses): SQLite returns the
        # bare columns of the MAX(created_at) row.
        rows = conn.execute(
            """SELECT *, MAX(created_at) FROM payments WHERE account_id = ?
               GROUP BY chain, asset, address
               HAVING SUM(received != '0') = 0 AND SUM(status != 'expired') = 0""", (acct["id"],)).fetchall()

        def look(p):
            try:
                return chain_client.observe(p["chain"], p["asset"], p["address"],
                                            ASSETS[(p["chain"], p["asset"])]["confirmations"])
            except chain_client.ChainError:
                return None

        found = failed = 0
        now = datetime.now(timezone.utc)
        for p, seen in zip(rows, pool_map(look, rows)):
            if seen is None:
                failed += 1
            elif seen[0] > 0:
                payments.apply_observation(conn, p, seen[0], seen[1], now, payments.find_hashes(p, seen[0]))
                found += 1
    if found:
        _balance_cache.clear()
    return {"checked": len(rows) - failed, "found": found, "failed": failed}


# --- Wallet send ----------------------------------------------------------------------------------
# BTC / LTC / DOGE, native ETH / BNB / TRX, and USDT on Ethereum, BNB Smart Chain and Tron (plus the
# test networks). The server only reads balances and relays signed transactions. Planning, the confirm
# screen and signing happen in the offline frame, which also re-derives every listed address before
# signing and pins chain ids, token contracts and decimals itself. Docs: docs/api.md#wallet
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


def sendable(chain, asset=None):
    """(kind, asset, contract or None). Test networks exist only with GATEWAY_TESTNETS=1."""
    if chain in UTXO_CHAINS:
        kind, native = "utxo", chain
    elif chain in EVM_CHAINS:
        kind, native = "evm", EVM_CHAINS[chain]["native"]
    elif chain in TRON_CHAINS:
        kind, native = "tron", "TRX"
    else:
        kind = native = None
    asset = (asset or native or "").upper()
    if not kind or (chain, asset) not in ASSETS or (kind == "utxo" and asset != native):
        fail(400, f"sending {asset} on {chain} isn't supported")
    return kind, asset, ASSETS[(chain, asset)]["contract"]


def tron_address_ok(text):
    try:
        raw = Base58Decoder.CheckDecode(text)
    except Exception:  # bip_utils raises its own checksum/format errors
        return False
    return len(raw) == 21 and raw[0] == 0x41


def pool_map(fn, items, workers=8):
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(fn, items))


@app.get("/wallet/send/{chain}")
def send_prepare(chain: str, asset: Optional[str] = None, to: Optional[str] = None, authorization: str = Header(None)):
    acct = account_for(authorization)
    rate_limit("wallet", acct["id"])
    chain = chain.upper()
    kind, asset, contract = sendable(chain, asset)
    xpub_chain = {"utxo": chain, "evm": "ETH", "tron": "TRX"}[kind]
    with closing(db.connect()) as conn:
        row = conn.execute("SELECT xpub FROM account_xpubs WHERE account_id = ? AND chain = ?",
                           (acct["id"], xpub_chain)).fetchone()
    if not row:
        fail(400, f"no {xpub_chain} xpub is registered on this account")
    xpub = db.unseal(row["xpub"], "xpub")
    addrs = wallet_addresses(acct["id"], xpub_chain, xpub)
    to = (to or "").strip()
    if (kind == "tron" or contract) and not to:
        fail(400, "`to` is required: fees depend on the destination")
    if to and kind == "evm" and not (len(to) == 42 and to.startswith("0x")):
        fail(400, "that isn't a valid address (0x…)")
    if to and kind == "tron" and not tron_address_ok(to):
        fail(400, "that isn't a valid Tron address (T…)")
    out = {"chain": chain, "kind": kind, "asset": asset}
    try:
        if kind == "utxo":
            found = pool_map(lambda a: chain_client.unspent(chain, a), addrs, 3)  # BlockCypher: ~3 requests/s without a token
            utxos = [{"address": a, "index": addrs[a], **u} for a, us in zip(addrs, found) for u in us]
            return {**out, "utxos": utxos, "fee_per_byte": chain_client.fee_rates(chain), "change_index": MAIN_INDEX,
                    "change_address": address(chain, xpub, MAIN_INDEX)}

        if kind == "evm":
            out.update(chain_id=EVM_CHAINS[chain]["chain_id"], gas_limit=EVM_GAS_LIMIT, fees=chain_client.evm_fees(chain))
            accounts = pool_map(lambda a: chain_client.evm_account(chain, a), addrs)
            listed = [{"address": a, "index": addrs[a], "balance": str(bal), "nonce": nonce}
                      for a, (bal, nonce) in zip(addrs, accounts)]
            if not contract:  # each funded address sends its own transfer and pays its own fee
                return {**out, "sources": [s for s in listed if s["balance"] != "0"]}
            tokens = pool_map(lambda a: chain_client.token_balance(chain, contract, a), addrs)
            for s, t in zip(listed, tokens):
                s["token_balance"] = str(t)
            funded = [s for s in listed if s["token_balance"] != "0"]
            gas = pool_map(lambda s: chain_client.evm_token_gas(chain, contract, s["address"], to, int(s["token_balance"])), funded)
            for s, g in zip(funded, gas):
                s["token_gas"] = g
            # The main address pays gas: it tops up each payment address that lacks it.
            return {**out, "main": next(s for s in listed if s["index"] == MAIN_INDEX), "sources": funded}

        out.update(params=chain_client.tron_params(chain), block=chain_client.tron_block(chain),
                   to_active=chain_client.tron_account(chain, to)["active"] if to else False)
        accounts = pool_map(lambda a: chain_client.tron_account(chain, a), addrs, 4)  # TronGrid: ~15 requests/s keyless
        listed = [{"address": a, "index": addrs[a], **{k: (str(v) if k == "balance" else v) for k, v in acct_.items()}}
                  for a, acct_ in zip(addrs, accounts)]
        if not contract:
            return {**out, "sources": [s for s in listed if s["balance"] != "0"]}
        tokens = pool_map(lambda a: chain_client.observe(chain, asset, a, 1)[0], addrs, 4)
        for s, t in zip(listed, tokens):
            s["token_balance"] = str(t)
        funded = [s for s in listed if s["token_balance"] != "0"]
        energy = pool_map(lambda s: chain_client.tron_token_energy(chain, contract, s["address"], to, int(s["token_balance"])), funded, 4)
        for s, e in zip(funded, energy):
            s["token_energy"] = e
        return {**out, "main": next(s for s in listed if s["index"] == MAIN_INDEX), "sources": funded}
    except chain_client.ChainError as e:
        fail(502, f"couldn't read the {chain} network right now ({e}); try again in a minute")


class Broadcast(BaseModel):
    txs: list[str] = Field(min_length=1, max_length=50)  # signed transactions, hex


@app.post("/wallet/send/{chain}")
def send_broadcast(chain: str, body: Broadcast, authorization: str = Header(None)):
    rate_limit("wallet", account_for(authorization)["id"])  # ponytail: any signed tx is relayed; signing protects funds
    chain = chain.upper()
    kind = sendable(chain)[0]
    txs = [t.strip().removeprefix("0x") for t in body.txs]
    if any(len(t) < 20 or len(t) > 200_000 or any(c not in "0123456789abcdefABCDEF" for c in t) for t in txs):
        fail(422, "each transaction must be hex")
    relay = {"utxo": chain_client.broadcast, "evm": chain_client.evm_broadcast, "tron": chain_client.tron_broadcast}[kind]
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


def num(value):
    return None if value is None else format(value.normalize(), "f")


@app.get("/market")
def market(request: Request, currency: str = "USD"):
    """Price and 24 h change per coin, for the wallet's display (never for pricing a payment)."""
    rate_limit("market", client_ip(request))
    if currency not in rates.CURRENCIES:
        fail(400, f"currency must be one of {', '.join(rates.CURRENCIES)}")
    return {"currency": currency, "coins": {coin: {"price": num(m["price"]), "change_24h": num(m["change_24h"])}
                                            for coin, m in rates.market(currency).items()}}


@app.get("/market/{coin}/history")
def market_history(coin: str, request: Request, span: str = "1D", currency: str = "USD"):
    """Closing prices for the wallet's chart: [[unix time, price], ...], oldest first."""
    rate_limit("market", client_ip(request))
    coin, span = coin.upper(), span.upper()
    if coin not in rates.COINS or span not in rates.SPANS or currency not in rates.CURRENCIES:
        fail(404, "unknown coin, span or currency")
    return {"coin": coin, "span": span, "currency": currency,
            "points": [[t, num(p)] for t, p in rates.history(coin, span, currency)]}


@app.get("/wallet/tx/{chain}/{tx_hash}")
def tx_status(chain: str, tx_hash: str, authorization: str = Header(None)):
    """Has a relayed transaction landed? Used to wait for gas top-ups before sending tokens."""
    rate_limit("wallet_tx", account_for(authorization)["id"])
    chain = chain.upper()
    if sendable(chain)[0] == "utxo":
        fail(400, "not available for this chain")
    h = tx_hash.lower().removeprefix("0x")
    if len(h) != 64 or any(c not in "0123456789abcdef" for c in h):
        fail(422, "bad transaction hash")
    try:
        ok = chain_client.tx_status(chain, ("0x" + h) if chain in EVM_CHAINS else h)
    except chain_client.ChainError as e:
        fail(502, f"couldn't read the {chain} network right now ({e})")
    return {"done": ok is not None, "ok": bool(ok)}


# --- Payments -------------------------------------------------------------------------------------

class NewPayment(BaseModel):
    # Either an amount in a coin (chain + asset + amount), or a price (price + currency [+ accept]):
    chain: Optional[str] = None
    asset: Optional[str] = None
    amount: Optional[str] = None
    price: Optional[str] = None
    currency: Optional[str] = None
    accept: Optional[list] = None
    reference: Optional[str] = None
    return_url: Optional[str] = None


@app.post("/payments", status_code=201)
def create_payment(body: NewPayment, response: Response, authorization: str = Header(None),
                   idempotency_key: Optional[str] = Header(None)):
    acct = account_for(authorization)
    rate_limit("payments", acct["id"])
    idem = None
    if idempotency_key is not None:
        if not 1 <= len(idempotency_key) <= 100:
            fail(400, "Idempotency-Key must be 1 to 100 characters")
        digest = hashlib.sha256(json.dumps(body.model_dump(), sort_keys=True).encode()).hexdigest()
        idem = {"key": idempotency_key, "hash": digest}
    priced = body.price is not None or body.currency is not None or body.accept is not None
    if priced and (body.chain or body.asset or body.amount):
        fail(400, "send either chain + asset + amount, or price + currency (the payer picks the coin), not both")
    if not priced and not (body.chain and body.asset and body.amount):
        fail(400, "chain, asset and amount are required (or price and currency)")
    with closing(db.connect()) as conn:
        try:
            if priced:
                p = payments.create_request(conn, acct["id"], body.price, body.currency, body.accept, body.reference,
                                            return_url=body.return_url, idempotency=idem)
            else:
                p = payments.create(conn, acct["id"], body.chain, body.asset, body.amount, body.reference,
                                    return_url=body.return_url, idempotency=idem)
        except payments.IdempotencyConflict as e:
            fail(409, str(e))
        except (AmountError, payments.PaymentError) as e:
            fail(400, str(e))
    if idem and idem.get("replayed"):
        response.status_code = 200  # the payment from the first request with this key
        response.headers["Idempotent-Replayed"] = "true"
    return view(p)


def view(row):
    """API view of a payment, or of a price request while no coin is chosen."""
    return payments.public(row) if "address" in row.keys() else payments.public_request(row)


@app.get("/payments/{payment_id}")
def get_payment(payment_id: str, authorization: str = Header(None)):
    acct = account_for(authorization)
    with closing(db.connect()) as conn:
        p = payments.lookup_any(conn, acct["id"], payment_id)
    if not p:
        fail(404, "payment not found")
    return view(p)


@app.get("/payments")
def list_payments(status: Optional[str] = None, limit: int = 50, authorization: str = Header(None)):
    acct = account_for(authorization)
    limit = max(1, min(limit, 100))
    query, params = "SELECT * FROM payments WHERE account_id = ?", [acct["id"]]
    if status:
        query, params = query + " AND status = ?", params + [status]
    with closing(db.connect()) as conn:
        rows = conn.execute(query + " ORDER BY created_at DESC, rowid DESC LIMIT ?", (*params, limit)).fetchall()
        # Price requests still waiting for the payer to pick a coin (once picked, they're payments above).
        waiting = conn.execute("""SELECT * FROM price_requests r WHERE account_id = ?
                                  AND NOT EXISTS (SELECT 1 FROM payments p WHERE p.id = r.id)
                                  ORDER BY created_at DESC LIMIT ?""", (acct["id"], limit)).fetchall()
    out = [payments.public(p) for p in rows] + [payments.public_request(r) for r in waiting]
    if status:
        out = [p for p in out if p["status"] == status]
    out.sort(key=lambda p: p["created_at"], reverse=True)  # same "YYYY-MM-DDTHH:MM:SSZ" format: sorts as text
    return {"payments": out[:limit]}


# --- Checkout (public, for the payer) ---------------------------------------------------------------

@app.get("/pay/{payment_id}/status")
def checkout_status(payment_id: str, request: Request):
    rate_limit("checkout", client_ip(request))  # ids are 128-bit random; this only stops floods
    with closing(db.connect()) as conn:
        p = conn.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
        req = None if p else conn.execute("SELECT * FROM price_requests WHERE id = ?", (payment_id,)).fetchone()
        owner = p or req
        shop = owner and conn.execute("SELECT shop_name, shop_logo IS NOT NULL AS logo FROM accounts WHERE id = ?",
                                      (owner["account_id"],)).fetchone()
    if not owner:
        fail(404, "payment not found")
    return {**checkout_view(p, req), "shop_name": shop["shop_name"],
            "logo_url": f"/pay/{payment_id}/logo" if shop["logo"] else None}


def checkout_view(p, req):
    if p:
        return payments.checkout(p)
    now = datetime.now(timezone.utc)
    out = {k: v for k, v in payments.public_request(req, now).items() if k not in ("reference", "checkout_url")}
    out["options"] = payments.options(req) if out["status"] == "choosing" else []
    out["server_time"] = db.at(now)
    return out


class Choice(BaseModel):
    chain: str
    asset: str


@app.post("/pay/{payment_id}/choose")
def checkout_choose(payment_id: str, body: Choice, request: Request):
    """The payer picks a coin for a payment priced in USD/EUR: the rate is locked now, the address shown."""
    rate_limit("checkout", client_ip(request))
    with closing(db.connect()) as conn:
        try:
            payments.choose(conn, payment_id, body.chain, body.asset)
        except payments.ChoiceError as e:
            fail(e.status, str(e))
        except (AmountError, payments.PaymentError) as e:
            fail(400, str(e))
    return checkout_status(payment_id, request)


@app.get("/pay/{payment_id}/logo", include_in_schema=False)
def checkout_logo(payment_id: str, request: Request):
    """The shop's logo, served from our domain so the checkout page loads nothing from elsewhere."""
    rate_limit("checkout", client_ip(request))
    with closing(db.connect()) as conn:
        row = conn.execute("""SELECT a.shop_logo, a.shop_logo_type FROM accounts a WHERE a.id IN
                              (SELECT account_id FROM payments WHERE id = ? UNION SELECT account_id FROM price_requests WHERE id = ?)""",
                           (payment_id, payment_id)).fetchone()
    if not row or not row["shop_logo"]:
        fail(404, "no logo")
    return Response(base64.b64decode(row["shop_logo"]), media_type=row["shop_logo_type"],
                    headers={"Content-Security-Policy": "default-src 'none'; frame-ancestors 'self'"})


@app.post("/hooks/blockcypher/{payment_id}", include_in_schema=False)
def blockcypher_hook(payment_id: str, request: Request, t: str = ""):
    """BlockCypher says something happened on this payment's address. Never trusted as proof: anyone can
    call this URL. It only makes the watcher check that payment on its next pass (every 5 s)."""
    rate_limit("hook", client_ip(request))
    if not hmac.compare_digest(t, payments.hook_token(payment_id)):
        fail(404, "not found")
    with closing(db.connect()) as conn, conn:
        conn.execute("UPDATE payments SET next_check_at = ? WHERE id = ? AND status IN ('pending', 'confirming', 'expired')",
                     (db.now(), payment_id))
    return Response(status_code=204)


@app.get("/pay/{payment_id}", include_in_schema=False)
def checkout_page(payment_id: str):
    return FileResponse(PUBLIC / "pay.html")  # the page loads its data from /pay/{id}/status


# Frontend (onboarding pages). Mounted last so API routes win.
app.mount("/", StaticFiles(directory=PUBLIC, html=True), name="static")
