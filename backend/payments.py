"""Payment creation (address allocation) and the status state machine.

Docs: docs/architecture.md#payments
"""
import json
import os
import secrets
from datetime import datetime, timedelta, timezone

import db
from assets import from_units, lookup, to_units
from xpubs import address

WINDOW = timedelta(minutes=30)        # time the customer has to send the payment
CONFIRM_GRACE = timedelta(hours=24)   # how long a seen-but-unconfirmed payment is still watched
LATE_WATCH = timedelta(hours=24)      # expired payments are still watched this long, for late funds
# ponytail: one slow pace for all chains; 96 provider calls per expired payment per day.
# BlockCypher push hooks (roadmap) would replace polling for BTC/LTC/DOGE if quota gets tight.
LATE_CHECK = timedelta(minutes=15)
CHECK_EVERY = {"BTC": 60, "LTC": 60, "DOGE": 60, "ETH": 15, "BSC": 10, "TRX": 10, "SEPOLIA": 12, "NILE": 10}  # seconds
OPEN = ("pending", "confirming")
EVENTS = {"confirming": "payment.confirming", "confirmed": "payment.confirmed", "expired": "payment.expired"}
PUBLIC_URL = os.getenv("GATEWAY_PUBLIC_URL", "http://localhost:8000").rstrip("/")  # base of checkout links
# What the payer's checkout page may see. Not `reference` (the merchant's own order data).
CHECKOUT_FIELDS = ("id", "status", "chain", "asset", "address", "amount", "amount_received", "amount_confirmed",
                   "confirmations_required", "token_contract", "created_at", "expires_at", "confirmed_at")


class PaymentError(ValueError):
    pass


def _ts(text):
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def create(conn, account_id, chain, asset, amount, reference=None, now=None):
    """Create a payment and give it a receive address. Returns the payment row."""
    (chain, asset), spec = lookup(chain, asset)
    units = to_units(amount, spec["decimals"])
    if reference is not None and (not isinstance(reference, str) or len(reference) > 100):
        raise PaymentError("reference must be a string of at most 100 characters")
    now = now or datetime.now(timezone.utc)

    with conn:
        conn.execute("BEGIN IMMEDIATE")  # one writer at a time: no two payments get the same new index
        row = conn.execute(
            "SELECT xpub, next_index FROM account_xpubs WHERE account_id = ? AND chain = ?",
            (account_id, spec["xpub"]),
        ).fetchone()
        if not row:
            raise PaymentError(f"no {spec['xpub']} xpub is registered on this account")

        # Every payment gets a never-used address. The gateway's wallet tracks every one of them, so
        # the gap limit of other wallets doesn't matter here (docs/architecture.md#address-allocation).
        index = row["next_index"]
        addr = address(spec["xpub"], row["xpub"], index)
        conn.execute(
            "UPDATE account_xpubs SET next_index = next_index + 1 WHERE account_id = ? AND chain = ?",
            (account_id, spec["xpub"]),
        )

        payment_id = "pay_" + secrets.token_urlsafe(16)
        stamp = db.at(now)
        conn.execute(
            """INSERT INTO payments (id, account_id, chain, asset, xpub_chain, address_index, address,
                                     amount, status, reference, created_at, expires_at, updated_at, next_check_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)""",
            (payment_id, account_id, chain, asset, spec["xpub"], index, addr, str(units),
             reference, stamp, db.at(now + WINDOW), stamp, stamp),
        )
    return get(conn, account_id, payment_id)


def get(conn, account_id, payment_id):
    return conn.execute(
        "SELECT * FROM payments WHERE id = ? AND account_id = ?", (payment_id, account_id)
    ).fetchone()


def next_status(p, seen, confirmed, now):
    """Pure state machine. p: payment row; seen/confirmed: received smallest units (int)."""
    if p["status"] not in OPEN:
        return p["status"]
    amount, expires = int(p["amount"]), _ts(p["expires_at"])
    if confirmed >= amount:
        return "confirmed"
    if seen >= amount and now <= expires + CONFIRM_GRACE:
        return "confirming"  # paid in full in time; waiting for confirmations
    if now > expires:
        return "expired"
    return "pending"


def apply_observation(conn, p, seen, confirmed, now):
    """Store what the chain showed; queue a webhook if the status changed, or `payment.paid_late` if
    more confirmed funds arrived on an expired payment. Returns the new status."""
    if p["status"] == "expired":
        # EVM/Tron observe a balance, which drops when the merchant sends the coins on. Keep the
        # high-water mark so an expired payment never "loses" what it received.
        # ponytail: new late funds after such a send are missed until they exceed the old amount.
        seen, confirmed = max(seen, int(p["received"])), max(confirmed, int(p["received_confirmed"]))
    status = next_status(p, seen, confirmed, now)
    stamp = db.at(now)
    pace = LATE_CHECK if status == "expired" else timedelta(seconds=CHECK_EVERY[p["chain"]])
    next_check = db.at(now + pace)
    with conn:
        updated = conn.execute(
            """UPDATE payments SET received = ?, received_confirmed = ?, status = ?, updated_at = ?,
                      next_check_at = ?, confirmed_at = CASE WHEN ? = 'confirmed' THEN ? ELSE confirmed_at END
               WHERE id = ? AND status = ? AND received_confirmed = ?""",  # never overwrite a concurrent change
            (str(seen), str(confirmed), status, stamp, next_check, status, stamp, p["id"], p["status"],
             p["received_confirmed"]),
        ).rowcount
        # Only the writer that actually made the change queues the event: no duplicate webhooks.
        if updated and status != p["status"]:
            queue_webhook(conn, p["id"], EVENTS[status], now)
        elif updated and p["status"] == "expired" and confirmed > int(p["received_confirmed"]):
            queue_webhook(conn, p["id"], "payment.paid_late", now)
    return status


def queue_webhook(conn, payment_id, event, now):
    p = conn.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
    acct = conn.execute("SELECT webhook_url FROM accounts WHERE id = ?", (p["account_id"],)).fetchone()
    if not acct["webhook_url"]:
        return
    body = json.dumps({"event": event, "payment": public(p)}, separators=(",", ":"), sort_keys=True)
    conn.execute(
        "INSERT INTO webhook_events (account_id, payment_id, event, body, next_attempt_at) VALUES (?, ?, ?, ?, ?)",
        (p["account_id"], payment_id, event, body, db.at(now)),
    )


def public(p):
    """API representation of a payment row (amounts as decimal strings)."""
    _, spec = lookup(p["chain"], p["asset"])
    d = spec["decimals"]
    return {
        "id": p["id"],
        "status": p["status"],
        "chain": p["chain"],
        "asset": p["asset"],
        "address": p["address"],
        "amount": from_units(p["amount"], d),
        "amount_received": from_units(p["received"], d),
        "amount_confirmed": from_units(p["received_confirmed"], d),
        "confirmations_required": spec["confirmations"],
        "token_contract": spec["contract"],
        "reference": p["reference"],
        "created_at": p["created_at"],
        "expires_at": p["expires_at"],
        "confirmed_at": p["confirmed_at"],
        "checkout_url": f"{PUBLIC_URL}/pay/{p['id']}",
    }


def checkout(p, now=None):
    """What the public checkout page gets: no API key, the unguessable payment id is the access token."""
    _, spec = lookup(p["chain"], p["asset"])
    out = {k: v for k, v in public(p).items() if k in CHECKOUT_FIELDS}
    out["amount_due"] = from_units(max(0, int(p["amount"]) - int(p["received"])), spec["decimals"])
    out["server_time"] = db.at(now or datetime.now(timezone.utc))  # lets the page correct its clock for the countdown
    return out
