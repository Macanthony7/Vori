"""SQLite connection, schema and tiny migrations. Shared by app.py and watcher.py.

Schema reference: docs/architecture.md#data-model
"""
import base64
import hashlib
import hmac
import logging
import os
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from dotenv import load_dotenv

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")  # every module imports db first; real environment variables win
# ponytail: SQLite (stdlib) for the MVP; move to Postgres/MySQL when running more than one server process.
DB_PATH = os.getenv("GATEWAY_DB", str(HERE / "gateway.db"))


# --- Secrets at rest: xpubs and webhook secrets are AES-256-GCM encrypted with GATEWAY_DB_KEY, which
# lives in the environment / backend/.env, never in the database. A copy of gateway.db alone then
# doesn't reveal merchants' wallets or let anyone forge webhooks. Docs: docs/security.md#database
def _master_key():
    text = os.getenv("GATEWAY_DB_KEY", "")
    if not text:
        # First start: make one and save it next to the code (backend/.env, gitignored).
        text = secrets.token_hex(32)
        with open(HERE / ".env", "a", encoding="utf-8") as f:
            f.write(f"\n# Encrypts xpubs and webhook secrets in the database. Back it up apart from gateway.db:\n"
                    f"# without it the stored xpubs can't be read.\nGATEWAY_DB_KEY={text}\n")
        os.environ["GATEWAY_DB_KEY"] = text
        logging.getLogger("db").warning("generated GATEWAY_DB_KEY in backend/.env; back it up apart from the database")
    key = bytes.fromhex(text)
    if len(key) != 32:
        raise RuntimeError("GATEWAY_DB_KEY must be 64 hex characters (32 bytes)")
    return key


_MASTER = _master_key()
_ENC = AESGCM(hmac.digest(_MASTER, b"gateway-db-encrypt", hashlib.sha256))
_TAG = hmac.digest(_MASTER, b"gateway-db-lookup", hashlib.sha256)
SEALED = "v1:"


def seal(text, label):
    """Encrypt `text`; `label` (e.g. "xpub") binds it to its column so values can't be swapped."""
    nonce = os.urandom(12)
    return SEALED + base64.b64encode(nonce + _ENC.encrypt(nonce, text.encode(), label.encode())).decode()


def unseal(blob, label):
    if blob is None:
        return None
    raw = base64.b64decode(blob[len(SEALED):])
    return _ENC.decrypt(raw[:12], raw[12:], label.encode()).decode()  # raises if the key is wrong


def tag(text):
    """Deterministic keyed hash, for finding and de-duplicating xpubs without storing them in clear."""
    return hmac.new(_TAG, text.encode(), hashlib.sha256).hexdigest()


SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id              INTEGER PRIMARY KEY,
    api_key_hash    TEXT NOT NULL UNIQUE,      -- sha256 of the API key; the key itself is never stored
    plan            TEXT NOT NULL DEFAULT 'free',
    plan_expires_at TEXT,
    created_at      TEXT NOT NULL,
    webhook_url     TEXT,
    webhook_secret  TEXT,                      -- HMAC key for webhook signatures, sealed (must be stored to sign)
    api_key_hint    TEXT,                      -- first characters of the current key ("gw_Xk3a"), to recognize it
    api_key_used_at TEXT,                      -- last request with the API key (updated at most once a minute)
    wallet_login_at TEXT,                      -- first wallet sign-in; from then on settings need a session
    shop_name       TEXT,                      -- shown on the checkout page
    shop_logo       TEXT,                      -- PNG/JPEG bytes, base64 (at most 100 KB)
    shop_logo_type  TEXT                       -- image/png | image/jpeg
);
CREATE TABLE IF NOT EXISTS account_xpubs (
    account_id  INTEGER NOT NULL REFERENCES accounts(id),
    chain       TEXT NOT NULL,                 -- xpub family: BTC, LTC, DOGE, ETH (also BSC), TRX
    xpub        TEXT NOT NULL,                 -- normalized account xpub, sealed (seal(x, "xpub"))
    xpub_tag    TEXT,                          -- tag(xpub): login lookup and "already registered"
    next_index  INTEGER NOT NULL DEFAULT 0,    -- next never-used receive address index
    PRIMARY KEY (account_id, chain)
);
CREATE UNIQUE INDEX IF NOT EXISTS account_xpubs_tag ON account_xpubs(xpub_tag);
CREATE TABLE IF NOT EXISTS payments (
    id                  TEXT PRIMARY KEY,      -- "pay_..."
    account_id          INTEGER NOT NULL REFERENCES accounts(id),
    chain               TEXT NOT NULL,         -- BTC LTC DOGE ETH BSC TRX
    asset               TEXT NOT NULL,         -- BTC LTC DOGE ETH BNB TRX USDT
    xpub_chain          TEXT NOT NULL,         -- which xpub the address comes from (BSC -> ETH)
    address_index       INTEGER NOT NULL,
    address             TEXT NOT NULL,
    amount              TEXT NOT NULL,         -- integer smallest units, as text (wei overflows int64)
    received            TEXT NOT NULL DEFAULT '0',  -- seen on chain, incl. unconfirmed
    received_confirmed  TEXT NOT NULL DEFAULT '0',  -- with the required confirmations
    status              TEXT NOT NULL,         -- pending | confirming | confirmed | expired
    reference           TEXT,                  -- merchant's own order id
    created_at          TEXT NOT NULL,
    expires_at          TEXT NOT NULL,
    confirmed_at        TEXT,
    updated_at          TEXT NOT NULL,
    next_check_at       TEXT NOT NULL,
    tx_hashes           TEXT NOT NULL DEFAULT '[]', -- JSON list of incoming transaction hashes, oldest first
    return_url          TEXT,                  -- "Back to shop" link on the checkout page
    price               TEXT,                  -- for payments priced in USD/EUR: the price, its currency,
    currency            TEXT,                  -- and the rate used (price of 1 coin) when the payer chose
    rate                TEXT,
    bc_hook             TEXT                   -- BlockCypher push hook: NULL not tried, "none" unavailable, "done" removed, else its id
);
CREATE INDEX IF NOT EXISTS payments_due ON payments(status, next_check_at);
CREATE INDEX IF NOT EXISTS payments_account ON payments(account_id, created_at);
CREATE INDEX IF NOT EXISTS payments_address ON payments(account_id, xpub_chain, address_index);
CREATE TABLE IF NOT EXISTS idempotency_keys (  -- POST /payments retries return the same payment for 24 h
    account_id  INTEGER NOT NULL REFERENCES accounts(id),
    key         TEXT NOT NULL,                 -- the Idempotency-Key header
    body_hash   TEXT NOT NULL,                 -- sha256 of the request, to refuse the same key for another order
    payment_id  TEXT NOT NULL,                 -- a payment, or a price request (no coin chosen yet)
    created_at  TEXT NOT NULL,
    PRIMARY KEY (account_id, key)
);
CREATE TABLE IF NOT EXISTS price_requests (  -- priced in USD/EUR; the payer picks the coin on the checkout page
    id          TEXT PRIMARY KEY,              -- "pay_...": becomes the payment's id once a coin is chosen
    account_id  INTEGER NOT NULL REFERENCES accounts(id),
    price       TEXT NOT NULL,                 -- decimal string, e.g. "25.00"
    currency    TEXT NOT NULL,                 -- USD | EUR
    accept      TEXT NOT NULL,                 -- JSON list of "CHAIN/ASSET" the payer may choose
    reference   TEXT,
    return_url  TEXT,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL                  -- choose a coin by then
);
CREATE TABLE IF NOT EXISTS webhook_events (
    id              INTEGER PRIMARY KEY,
    account_id      INTEGER NOT NULL REFERENCES accounts(id),
    payment_id      TEXT NOT NULL REFERENCES payments(id),
    event           TEXT NOT NULL,             -- payment.confirming | payment.confirmed | payment.expired
    body            TEXT NOT NULL,             -- exact JSON that gets signed and sent
    attempts        INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,                      -- NULL once delivered or given up
    delivered_at    TEXT,
    last_error      TEXT
);
CREATE INDEX IF NOT EXISTS webhook_due ON webhook_events(next_attempt_at);
CREATE TABLE IF NOT EXISTS login_challenges (
    nonce       TEXT PRIMARY KEY,              -- single use; deleted when used
    expires_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT PRIMARY KEY,              -- sha256 of the "gws_..." token
    account_id  INTEGER NOT NULL REFERENCES accounts(id),
    expires_at  TEXT NOT NULL
);
"""

# Columns added after 0.1.0; CREATE TABLE IF NOT EXISTS won't add them to an existing database.
MIGRATIONS = [("accounts", "webhook_url", "TEXT"), ("accounts", "webhook_secret", "TEXT"),
              ("payments", "tx_hashes", "TEXT NOT NULL DEFAULT '[]'"), ("account_xpubs", "xpub_tag", "TEXT"),
              ("accounts", "api_key_hint", "TEXT"), ("accounts", "api_key_used_at", "TEXT"),
              ("accounts", "wallet_login_at", "TEXT"), ("accounts", "shop_name", "TEXT"),
              ("accounts", "shop_logo", "TEXT"), ("accounts", "shop_logo_type", "TEXT"), ("payments", "return_url", "TEXT"),
              ("payments", "price", "TEXT"), ("payments", "currency", "TEXT"), ("payments", "rate", "TEXT"),
              ("payments", "bc_hook", "TEXT")]


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init():
    with closing(connect()) as conn:
        conn.execute("PRAGMA journal_mode = WAL")  # API and watcher write concurrently
        for table, column, kind in MIGRATIONS:
            exists = conn.execute(f"SELECT 1 FROM sqlite_master WHERE type='table' AND name='{table}'").fetchone()
            cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            if exists and column not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
        conn.executescript(SCHEMA)
        # Encrypt anything stored before encryption existed (idempotent: sealed values start with v1:).
        with conn:
            for r in conn.execute("SELECT account_id, chain, xpub FROM account_xpubs WHERE xpub NOT LIKE 'v1:%'").fetchall():
                conn.execute("UPDATE account_xpubs SET xpub = ?, xpub_tag = ? WHERE account_id = ? AND chain = ?",
                             (seal(r["xpub"], "xpub"), tag(r["xpub"]), r["account_id"], r["chain"]))
            for r in conn.execute("SELECT id, webhook_secret FROM accounts WHERE webhook_secret NOT LIKE 'v1:%'").fetchall():
                conn.execute("UPDATE accounts SET webhook_secret = ? WHERE id = ?",
                             (seal(r["webhook_secret"], "webhook_secret"), r["id"]))


def now():
    """UTC timestamp as a sortable string. Every *_at column uses this format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def at(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
