"""Payment creation (address allocation) and the status state machine.

Docs: docs/architecture.md#payments
"""
import json
import secrets
from datetime import datetime, timedelta, timezone

import db
from assets import from_units, lookup, to_units
from xpubs import address

WINDOW = timedelta(minutes=30)        # time the customer has to send the payment
CONFIRM_GRACE = timedelta(hours=24)   # how long a seen-but-unconfirmed payment is still watched
REUSE_AFTER = timedelta(hours=24)     # quarantine before an unpaid address can be handed out again
CHECK_EVERY = {"BTC": 60, "LTC": 60, "DOGE": 60, "ETH": 15, "BSC": 10, "TRX": 10}  # seconds
OPEN = ("pending", "confirming")
EVENTS = {"confirming": "payment.confirming", "confirmed": "payment.confirmed", "expired": "payment.expired"}


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

        # Gap limit: wallets stop scanning after ~20 unused addresses in a row. Hand an address
        # back out only if every payment on it was for the same chain/asset, expired with nothing
        # received, and the last one expired over REUSE_AFTER ago.
        reuse = conn.execute(
            """SELECT address_index, address FROM payments
               WHERE account_id = ? AND xpub_chain = ?
               GROUP BY address_index
               HAVING SUM(status != 'expired' OR received != '0' OR chain != ? OR asset != ?) = 0
                  AND MAX(expires_at) < ?
               ORDER BY address_index LIMIT 1""",
            (account_id, spec["xpub"], chain, asset, db.at(now - REUSE_AFTER)),
        ).fetchone()
        if reuse:
            index, addr = reuse["address_index"], reuse["address"]
        else:
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
    """Store what the chain showed; queue a webhook if the status changed. Returns the new status."""
    status = next_status(p, seen, confirmed, now)
    stamp = db.at(now)
    next_check = db.at(now + timedelta(seconds=CHECK_EVERY[p["chain"]]))
    with conn:
        updated = conn.execute(
            """UPDATE payments SET received = ?, received_confirmed = ?, status = ?, updated_at = ?,
                      next_check_at = ?, confirmed_at = CASE WHEN ? = 'confirmed' THEN ? ELSE confirmed_at END
               WHERE id = ? AND status = ?""",  # status guard: never overwrite a concurrent change
            (str(seen), str(confirmed), status, stamp, next_check, status, stamp, p["id"], p["status"]),
        ).rowcount
        # Only the writer that actually made the transition queues the event: no duplicate webhooks.
        if updated and status != p["status"]:
            queue_webhook(conn, p["id"], EVENTS[status], now)
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
    }
