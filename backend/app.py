"""Non-custodial payment gateway API. Also serves the onboarding pages from ../frontend/public.

Run from backend/:  python -m uvicorn app:app --port 8000
Payments are watched by a separate process: python watcher.py
Docs: ../docs/api.md
"""
import hashlib
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import db
import payments
from assets import AmountError
from webhooks import WebhookError, check_url
from xpubs import CHAINS, XpubError, address, normalize

HERE = Path(__file__).parent
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
    if request.url.path.startswith(("/accounts", "/payments")):
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
                "INSERT INTO account_xpubs (account_id, chain, xpub) VALUES (?, ?, ?)",
                [(account_id, chain, x) for chain, x in normalized.items()],
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
    scheme, _, key = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not key:
        fail(401, "missing API key (Authorization: Bearer <key>)")
    with closing(db.connect()) as conn:
        row = conn.execute("SELECT * FROM accounts WHERE api_key_hash = ?", (hash_key(key.strip()),)).fetchone()
    if not row:
        fail(401, "invalid API key")
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


# Frontend (onboarding pages). Mounted last so API routes win.
app.mount("/", StaticFiles(directory=PUBLIC, html=True), name="static")
