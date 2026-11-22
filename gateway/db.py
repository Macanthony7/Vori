"""SQLite connection, schema and tiny migrations. Shared by app.py and watcher.py.

Schema reference: docs/architecture.md#data-model
"""
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
# ponytail: SQLite (stdlib) for the MVP; move to Postgres/MySQL when running more than one server process.
DB_PATH = os.getenv("GATEWAY_DB", str(HERE / "gateway.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id              INTEGER PRIMARY KEY,
    api_key_hash    TEXT NOT NULL UNIQUE,      -- sha256 of the API key; the key itself is never stored
    plan            TEXT NOT NULL DEFAULT 'free',
    plan_expires_at TEXT,
    created_at      TEXT NOT NULL,
    webhook_url     TEXT,
    webhook_secret  TEXT                       -- HMAC key for webhook signatures (must be stored to sign)
);
CREATE TABLE IF NOT EXISTS account_xpubs (
    account_id  INTEGER NOT NULL REFERENCES accounts(id),
    chain       TEXT NOT NULL,                 -- xpub family: BTC, LTC, DOGE, ETH (also BSC), TRX
    xpub        TEXT NOT NULL UNIQUE,          -- normalized account xpub
    next_index  INTEGER NOT NULL DEFAULT 0,    -- next never-used receive address index
    PRIMARY KEY (account_id, chain)
);
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
    next_check_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS payments_due ON payments(status, next_check_at);
CREATE INDEX IF NOT EXISTS payments_account ON payments(account_id, created_at);
CREATE INDEX IF NOT EXISTS payments_address ON payments(account_id, xpub_chain, address_index);
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
"""

# Columns added after 0.1.0; CREATE TABLE IF NOT EXISTS won't add them to an existing database.
MIGRATIONS = [("accounts", "webhook_url", "TEXT"), ("accounts", "webhook_secret", "TEXT")]


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


def now():
    """UTC timestamp as a sortable string. Every *_at column uses this format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def at(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
