"""Payment watcher: checks open payments on chain, updates their status, delivers webhooks.

Run next to the API:   python watcher.py          (loops forever)
Or from cron:          python watcher.py --once   (one pass)
Docs: docs/architecture.md#watcher
"""
import logging
import sys
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone

import chain_client
import db
import payments
import webhooks
from assets import ASSETS

log = logging.getLogger("watcher")
LOOP_SLEEP = 5  # seconds between passes; per-chain pacing is payments.CHECK_EVERY


def tick(conn, now=None, observe=chain_client.observe, send=webhooks.send, limit=100):
    """One pass. Returns (checked, changed, delivered)."""
    now = now or datetime.now(timezone.utc)
    due = conn.execute(
        "SELECT * FROM payments WHERE status IN ('pending', 'confirming') AND next_check_at <= ? "
        "ORDER BY next_check_at LIMIT ?",
        (db.at(now), limit),
    ).fetchall()
    changed = 0
    for p in due:
        try:
            seen, confirmed = observe(p["chain"], p["asset"], p["address"],
                                      ASSETS[(p["chain"], p["asset"])]["confirmations"])
        except chain_client.ChainError as e:
            # Can't see the chain: don't guess (not even "expired"). Retry on the normal schedule
            # instead of every pass, so a failing provider isn't hammered.
            log.warning("%s %s: %s", p["id"], p["chain"], e)
            with conn:
                conn.execute("UPDATE payments SET next_check_at = ? WHERE id = ?",
                             (db.at(now + timedelta(seconds=payments.CHECK_EVERY[p["chain"]])), p["id"]))
            continue
        status = payments.apply_observation(conn, p, seen, confirmed, now)
        if status != p["status"]:
            changed += 1
            log.info("%s %s/%s %s -> %s", p["id"], p["chain"], p["asset"], p["status"], status)
    delivered = webhooks.deliver_due(conn, now, send=send)
    return len(due), changed, delivered


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    db.init()
    once = "--once" in sys.argv
    while True:
        with closing(db.connect()) as conn:
            try:
                tick(conn)
            except Exception:
                log.exception("watcher pass failed")  # keep running; next pass retries
        if once:
            return
        time.sleep(LOOP_SLEEP)


if __name__ == "__main__":
    main()
