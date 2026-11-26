"""Payment creation (address allocation) and the status state machine.

Docs: docs/architecture.md#payments
"""
import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import urlsplit

import chain_client
import db
import rates
from assets import ASSETS, EXPLORERS, from_units, lookup, to_units
from xpubs import address

WINDOW = timedelta(minutes=30)        # time the customer has to send the payment
CONFIRM_GRACE = timedelta(hours=24)   # how long a seen-but-unconfirmed payment is still watched
LATE_WATCH = timedelta(hours=24)      # expired payments are still watched this long, for late funds
# ponytail: one slow pace for all chains; 96 provider calls per expired payment per day.
# BlockCypher push hooks (roadmap) would replace polling for BTC/LTC/DOGE if quota gets tight.
LATE_CHECK = timedelta(minutes=15)
IDEMPOTENCY_TTL = timedelta(hours=24)  # how long an Idempotency-Key returns the same payment
HOOKED_CHECK = timedelta(minutes=10)   # BTC/LTC/DOGE polling while a BlockCypher hook is active (it pokes us sooner)
CHECK_EVERY = {"BTC": 60, "LTC": 60, "DOGE": 60, "ETH": 15, "BSC": 10, "TRX": 10, "SEPOLIA": 12, "NILE": 10}  # seconds
OPEN = ("pending", "confirming")
EVENTS = {"confirming": "payment.confirming", "confirmed": "payment.confirmed", "expired": "payment.expired"}
PUBLIC_URL = os.getenv("GATEWAY_PUBLIC_URL", "http://localhost:8000").rstrip("/")  # base of checkout links
# What the payer's checkout page may see. Not `reference` (the merchant's own order data).
CHECKOUT_FIELDS = ("id", "status", "chain", "asset", "address", "amount", "amount_received", "amount_confirmed",
                   "confirmations_required", "token_contract", "created_at", "expires_at", "confirmed_at",
                   "address_url", "return_url", "price", "currency", "rate")  # no transaction hashes on the payer's page (merchant's choice)


class PaymentError(ValueError):
    pass


class IdempotencyConflict(PaymentError):
    pass


class ChoiceError(PaymentError):
    """Choosing a coin failed. .status: the HTTP status the API answers with."""
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _replay(conn, account_id, idempotency, now):
    """Inside the caller's transaction: the id this Idempotency-Key already created, or None."""
    if not idempotency:
        return None
    conn.execute("DELETE FROM idempotency_keys WHERE created_at < ?", (db.at(now - IDEMPOTENCY_TTL),))
    hit = conn.execute("SELECT body_hash, payment_id FROM idempotency_keys WHERE account_id = ? AND key = ?",
                       (account_id, idempotency["key"])).fetchone()
    if not hit:
        return None
    if hit["body_hash"] != idempotency["hash"]:
        raise IdempotencyConflict("this Idempotency-Key was already used for a different payment")
    idempotency["replayed"] = True
    return hit["payment_id"]


def _remember(conn, account_id, idempotency, payment_id, stamp):
    if idempotency:
        conn.execute("INSERT INTO idempotency_keys (account_id, key, body_hash, payment_id, created_at) "
                     "VALUES (?, ?, ?, ?, ?)", (account_id, idempotency["key"], idempotency["hash"], payment_id, stamp))


def check_return_url(url):
    """https only (http just for localhost, when developing a shop). Shown as a link the payer clicks;
    never followed by the server, never a redirect."""
    if not isinstance(url, str) or len(url) > 500:
        raise PaymentError("return_url must be a string of at most 500 characters")
    parts = urlsplit(url)
    local = parts.hostname in ("localhost", "127.0.0.1")
    if parts.scheme != "https" and not (parts.scheme == "http" and local):
        raise PaymentError("return_url must start with https://")
    if not parts.hostname or parts.username or parts.password or any(c in url for c in "\r\n\t <>\"'"):
        raise PaymentError("return_url must be a plain web address with a host")
    return url


def _ts(text):
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def create(conn, account_id, chain, asset, amount, reference=None, now=None, return_url=None, idempotency=None,
           payment_id=None, priced=None):
    """Create a payment and give it a receive address. Returns the payment row.

    idempotency: {"key": header value, "hash": sha256 of the request}. A repeat within IDEMPOTENCY_TTL
    returns the first payment and sets idempotency["replayed"] = True; the same key with a different
    request raises IdempotencyConflict. payment_id and priced=(price, currency, rate): used when a payer
    picks a coin for a price request (the payment keeps the request's id).
    """
    (chain, asset), spec = lookup(chain, asset)
    units = to_units(amount, spec["decimals"])
    if reference is not None and (not isinstance(reference, str) or len(reference) > 100):
        raise PaymentError("reference must be a string of at most 100 characters")
    if return_url is not None:
        check_return_url(return_url)
    now = now or datetime.now(timezone.utc)

    with conn:
        conn.execute("BEGIN IMMEDIATE")  # one writer at a time: no two payments get the same new index
        replay = _replay(conn, account_id, idempotency, now)
        if replay:
            return get(conn, account_id, replay)
        row = conn.execute(
            "SELECT xpub, next_index FROM account_xpubs WHERE account_id = ? AND chain = ?",
            (account_id, spec["xpub"]),
        ).fetchone()
        if not row:
            raise PaymentError(f"no {spec['xpub']} xpub is registered on this account")

        # Every payment gets a never-used address. The gateway's wallet tracks every one of them, so
        # the gap limit of other wallets doesn't matter here (docs/architecture.md#address-allocation).
        index = row["next_index"]
        addr = address(spec["xpub"], db.unseal(row["xpub"], "xpub"), index)
        conn.execute(
            "UPDATE account_xpubs SET next_index = next_index + 1 WHERE account_id = ? AND chain = ?",
            (account_id, spec["xpub"]),
        )

        payment_id = payment_id or "pay_" + secrets.token_urlsafe(16)
        price, currency, rate = priced or (None, None, None)
        stamp = db.at(now)
        conn.execute(
            """INSERT INTO payments (id, account_id, chain, asset, xpub_chain, address_index, address,
                                     amount, status, reference, created_at, expires_at, updated_at, next_check_at,
                                     return_url, price, currency, rate)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (payment_id, account_id, chain, asset, spec["xpub"], index, addr, str(units),
             reference, stamp, db.at(now + WINDOW), stamp, stamp, return_url, price, currency, rate),
        )
        _remember(conn, account_id, idempotency, payment_id, stamp)
    return get(conn, account_id, payment_id)


# --- BlockCypher push hooks (docs/architecture.md#blockcypher-push-hooks) ------------------------------
def has_hook(p):
    hook = p["bc_hook"] if "bc_hook" in p.keys() else None
    return bool(hook) and hook not in ("none", "done")


def hook_token(payment_id):
    """Secret part of a hook's callback URL. Derived from GATEWAY_DB_KEY, so nothing extra is stored."""
    return db.tag("blockcypher-hook:" + payment_id)[:32]


def hook_url(payment_id):
    return f"{PUBLIC_URL}/hooks/blockcypher/{payment_id}?t={hook_token(payment_id)}"


# --- Prices in USD/EUR: the payer picks the coin (docs/api.md#prices-in-usd-or-eur) ------------------
MAX_PRICE = Decimal("1000000")


def create_request(conn, account_id, price, currency, accept, reference=None, return_url=None, now=None,
                   idempotency=None):
    """A payment priced in USD/EUR with no coin yet. accept: ["CHAIN/ASSET", ...] or None for every coin
    the account can receive. Returns the price_requests row."""
    try:
        value = Decimal(price) if isinstance(price, str) else None
    except ArithmeticError:
        value = None
    if value is None or not value.is_finite() or value <= 0 or value > MAX_PRICE or -value.as_tuple().exponent > 2:
        raise PaymentError("price must be a decimal string above 0 with at most 2 decimal places, like \"25.00\"")
    currency = str(currency or "").upper()
    if currency not in rates.CURRENCIES:
        raise PaymentError(f"currency must be one of {', '.join(rates.CURRENCIES)}")
    if reference is not None and (not isinstance(reference, str) or len(reference) > 100):
        raise PaymentError("reference must be a string of at most 100 characters")
    if return_url is not None:
        check_return_url(return_url)
    families = {r["chain"] for r in conn.execute("SELECT chain FROM account_xpubs WHERE account_id = ?", (account_id,))}
    receivable = [f"{c}/{a}" for (c, a), spec in ASSETS.items() if spec["xpub"] in families]
    if accept is None:
        accept = receivable
    if not isinstance(accept, list) or not accept or len(accept) > len(ASSETS):
        raise PaymentError("accept must be a non-empty list like [\"BTC/BTC\", \"TRX/USDT\"]")
    accept = [str(a).upper() for a in accept]
    for pair in accept:
        if pair not in receivable:
            raise PaymentError(f"can't accept {pair}: unsupported, or no xpub registered for it")
    now = now or datetime.now(timezone.utc)
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        replay = _replay(conn, account_id, idempotency, now)
        if replay:
            return lookup_any(conn, account_id, replay)
        request_id = "pay_" + secrets.token_urlsafe(16)
        stamp = db.at(now)
        conn.execute("""INSERT INTO price_requests (id, account_id, price, currency, accept, reference, return_url,
                                                     created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                     (request_id, account_id, format(value.quantize(Decimal("0.01")), "f"), currency, json.dumps(list(dict.fromkeys(accept))),
                      reference, return_url, stamp, db.at(now + WINDOW)))
        _remember(conn, account_id, idempotency, request_id, stamp)
    return conn.execute("SELECT * FROM price_requests WHERE id = ?", (request_id,)).fetchone()


def lookup_any(conn, account_id, payment_id):
    """The payment, or its price request while no coin is chosen. None if neither (or not this account's)."""
    return get(conn, account_id, payment_id) or conn.execute(
        "SELECT * FROM price_requests WHERE id = ? AND account_id = ?", (payment_id, account_id)).fetchone()


def options(req):
    """The coins the payer can pick, each with its converted amount (None = no trusted rate right now)."""
    out = []
    for pair in json.loads(req["accept"]):
        chain, asset = pair.split("/")
        quote = rates.convert(req["price"], req["currency"], asset, ASSETS[(chain, asset)]["decimals"])
        out.append({"chain": chain, "asset": asset, "amount": quote[0] if quote else None, "available": bool(quote)})
    return out


def choose(conn, request_id, chain, asset, now=None):
    """The payer picked a coin: lock the rate now and create the payment (same id). Returns its row."""
    now = now or datetime.now(timezone.utc)
    req = conn.execute("SELECT * FROM price_requests WHERE id = ?", (request_id,)).fetchone()
    if not req:
        raise ChoiceError("payment not found", 404)
    if conn.execute("SELECT 1 FROM payments WHERE id = ?", (request_id,)).fetchone():
        raise ChoiceError("a coin was already chosen for this payment", 409)
    if now > _ts(req["expires_at"]):
        raise ChoiceError("this payment expired before a coin was chosen; ask the merchant for a new one", 410)
    pair = f"{str(chain).upper()}/{str(asset).upper()}"
    if pair not in json.loads(req["accept"]):
        raise ChoiceError(f"this shop doesn't accept {pair} for this payment")
    (chain, asset), spec = lookup(*pair.split("/"))
    quote = rates.convert(req["price"], req["currency"], asset, spec["decimals"])
    if not quote:
        raise ChoiceError(f"no reliable {asset} price right now; pick another coin or try again in a minute", 503)
    try:
        return create(conn, req["account_id"], chain, asset, quote[0], req["reference"], now=now,
                      return_url=req["return_url"], payment_id=request_id, priced=(req["price"], req["currency"], quote[1]))
    except sqlite3.IntegrityError:
        raise ChoiceError("a coin was already chosen for this payment", 409) from None  # two choices at once


def public_request(req, now=None):
    """API view of a price request while no coin is chosen: like a payment, without coin fields."""
    expired = (now or datetime.now(timezone.utc)) > _ts(req["expires_at"])
    return {"id": req["id"], "status": "expired" if expired else "choosing", "chain": None, "asset": None,
            "address": None, "amount": None, "price": req["price"], "currency": req["currency"],
            "accept": json.loads(req["accept"]), "reference": req["reference"], "return_url": req["return_url"],
            "created_at": req["created_at"], "expires_at": req["expires_at"],
            "checkout_url": f"{PUBLIC_URL}/pay/{req['id']}"}


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


def find_hashes(p, seen, lookup=None):
    """New transaction hashes when the amount went up, else None. Best effort: a failed lookup only
    means a missing explorer link (the address link still works), never a wrong status."""
    if seen <= int(p["received"]):
        return None
    lookup = lookup or chain_client.tx_hashes
    try:
        # Anything new arrived after the last observation (updated_at); EVM tokens search from there.
        return lookup(p["chain"], p["asset"], p["address"], _ts(p["updated_at"]))
    except chain_client.ChainError:
        return None


def apply_observation(conn, p, seen, confirmed, now, hashes=None):
    """Store what the chain showed (and new transaction hashes); queue a webhook if the status changed,
    or `payment.paid_late` if more confirmed funds arrived on an expired payment. Returns the new status."""
    if p["status"] == "expired":
        # EVM/Tron observe a balance, which drops when the merchant sends the coins on. Keep the
        # high-water mark so an expired payment never "loses" what it received.
        # ponytail: new late funds after such a send are missed until they exceed the old amount.
        seen, confirmed = max(seen, int(p["received"])), max(confirmed, int(p["received_confirmed"]))
    status = next_status(p, seen, confirmed, now)
    stamp = db.at(now)
    pace = LATE_CHECK if status == "expired" else timedelta(seconds=CHECK_EVERY[p["chain"]])
    if has_hook(p) and status != "expired":
        pace = HOOKED_CHECK  # the hook asks for a check when something happens; polling is only the fallback
    next_check = db.at(now + pace)
    known = json.loads(p["tx_hashes"])
    tx_hashes = json.dumps(list(dict.fromkeys(known + (hashes or []))))
    with conn:
        updated = conn.execute(
            """UPDATE payments SET received = ?, received_confirmed = ?, status = ?, updated_at = ?,
                      next_check_at = ?, tx_hashes = ?, confirmed_at = CASE WHEN ? = 'confirmed' THEN ? ELSE confirmed_at END
               WHERE id = ? AND status = ? AND received_confirmed = ?""",  # never overwrite a concurrent change
            (str(seen), str(confirmed), status, stamp, next_check, tx_hashes, status, stamp, p["id"], p["status"],
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
    tx_url, address_url = EXPLORERS[p["chain"]]
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
        # Incoming transactions, oldest first. Empty for native ETH/BNB (not searchable with plain
        # JSON-RPC) and until found; address_url then shows them on the explorer.
        "transactions": [{"hash": h, "url": tx_url + h} for h in json.loads(p["tx_hashes"])],
        "address_url": address_url + p["address"],
        "return_url": p["return_url"],
        "price": p["price"],          # set only for payments priced in USD/EUR
        "currency": p["currency"],
        "rate": p["rate"],
        "checkout_url": f"{PUBLIC_URL}/pay/{p['id']}",
    }


def checkout(p, now=None):
    """What the public checkout page gets: no API key, the unguessable payment id is the access token."""
    _, spec = lookup(p["chain"], p["asset"])
    out = {k: v for k, v in public(p).items() if k in CHECKOUT_FIELDS}
    out["amount_due"] = from_units(max(0, int(p["amount"]) - int(p["received"])), spec["decimals"])
    out["server_time"] = db.at(now or datetime.now(timezone.utc))  # lets the page correct its clock for the countdown
    return out
