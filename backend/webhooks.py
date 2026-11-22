"""Webhook URL validation, HMAC signing and delivery with retries.

Docs: docs/api.md#webhooks (format and how merchants verify), docs/security.md (SSRF rules)
"""
import hashlib
import hmac
import ipaddress
import os
import socket
import time
from datetime import timedelta
from urllib.parse import urlsplit

import requests

import db

RETRY_AFTER = [60, 300, 1800, 7200, 21600, 86400]  # seconds; gives up after the last one
TIMEOUT = 10
# Development only: allow http:// and private/loopback hosts (e.g. a local test receiver).
ALLOW_PRIVATE = os.getenv("GATEWAY_ALLOW_PRIVATE_WEBHOOKS") == "1"


class WebhookError(ValueError):
    pass


def check_url(url):
    """Reject URLs that would make our server call itself or an internal network (SSRF)."""
    if not isinstance(url, str) or len(url) > 500:
        raise WebhookError("url must be a string of at most 500 characters")
    parts = urlsplit(url)
    if parts.scheme not in (("https", "http") if ALLOW_PRIVATE else ("https",)):
        raise WebhookError("url must start with https://")
    if not parts.hostname or parts.username or parts.password:
        raise WebhookError("url must have a host and no credentials")
    if ALLOW_PRIVATE:
        return url
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise WebhookError("url host does not resolve")
    for info in infos:
        if not ipaddress.ip_address(info[4][0].split("%")[0]).is_global:
            raise WebhookError("url must point to a public internet address")
    return url


def sign(secret, body, timestamp):
    mac = hmac.new(secret.encode(), f"{timestamp}.{body}".encode(), hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={mac}"


def verify(secret, body, header, tolerance=300, now=None):
    """Reference verifier (what merchants implement). Returns True if the signature is valid and fresh."""
    try:
        fields = dict(part.split("=", 1) for part in header.split(","))
        timestamp = int(fields["t"])
    except (ValueError, KeyError):
        return False
    if abs((now or time.time()) - timestamp) > tolerance:
        return False
    return hmac.compare_digest(sign(secret, body, timestamp), header)


def send(url, secret, body):
    """POST one event. Returns None on success (2xx) or an error string."""
    try:
        check_url(url)  # re-check at send time: DNS may have changed since the URL was saved
        r = requests.post(
            url,
            data=body.encode(),
            headers={"Content-Type": "application/json", "Gateway-Signature": sign(secret, body, int(time.time()))},
            timeout=TIMEOUT,
            allow_redirects=False,
        )
    except (requests.RequestException, WebhookError) as e:
        return str(e)[:300]
    return None if 200 <= r.status_code < 300 else f"HTTP {r.status_code}"


def deliver_due(conn, now, send=send, limit=50):
    """Send every queued event that is due. Returns how many were delivered."""
    due = conn.execute(
        """SELECT e.*, a.webhook_url, a.webhook_secret FROM webhook_events e
           JOIN accounts a ON a.id = e.account_id
           WHERE e.next_attempt_at IS NOT NULL AND e.next_attempt_at <= ?
           ORDER BY e.id LIMIT ?""",
        (db.at(now), limit),
    ).fetchall()
    delivered = 0
    for e in due:
        error = "webhook removed" if not e["webhook_url"] else send(e["webhook_url"], e["webhook_secret"], e["body"])
        attempts = e["attempts"] + 1
        if error is None:
            delivered += 1
            row = (attempts, None, db.at(now), None)
        elif attempts > len(RETRY_AFTER) or not e["webhook_url"]:
            row = (attempts, None, None, error)  # give up
        else:
            row = (attempts, db.at(now + timedelta(seconds=RETRY_AFTER[attempts - 1])), None, error)
        with conn:
            conn.execute(
                "UPDATE webhook_events SET attempts = ?, next_attempt_at = ?, delivered_at = ?, last_error = ? WHERE id = ?",
                (*row, e["id"]),
            )
    return delivered
