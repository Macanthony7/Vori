"""Payment watcher: checks open payments on chain, updates their status, delivers webhooks.

Run as a service:        python watcher.py                    (loops until stopped)
From cron, one pass:     python watcher.py --once
From cPanel-style cron:  python watcher.py --max-seconds 280  (loop, exit before a 5-min limit;
                                                                schedule every minute)
Only one watcher runs at a time per database (lock file next to it); extra starts exit quietly.
Docs: docs/architecture.md#watcher
"""
import argparse
import logging
import os
import signal
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
_stop = False


def tick(conn, now=None, observe=chain_client.observe, send=webhooks.send, limit=100):
    """One pass. Returns (checked, changed, delivered)."""
    now = now or datetime.now(timezone.utc)
    due = conn.execute(
        "SELECT * FROM payments WHERE (status IN ('pending', 'confirming') OR (status = 'expired' AND expires_at > ?)) "
        "AND next_check_at <= ? ORDER BY next_check_at LIMIT ?",  # expired: late funds, for payments.LATE_WATCH
        (db.at(now - payments.LATE_WATCH), db.at(now), limit),
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


def single_instance(path):
    """Hold an OS file lock for the life of the process (released automatically on exit/crash).

    Returns the open file (keep a reference) or None if another watcher holds it.
    Carried over from the prototype's poller, which ran from cron on cPanel.
    """
    handle = open(path, "a+")
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def _request_stop(signum, frame):
    global _stop
    _stop = True  # finish the current pass, then exit


def main(argv=None):
    parser = argparse.ArgumentParser(description="Payment watcher")
    parser.add_argument("--once", action="store_true", help="run one pass and exit")
    parser.add_argument("--max-seconds", type=int, default=0,
                        help="loop, but exit after this many seconds (for cron with a time limit)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    lock = single_instance(db.DB_PATH + ".watcher.lock")
    if lock is None:
        log.info("another watcher is running; exiting")
        return
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _request_stop)

    db.init()
    deadline = time.monotonic() + args.max_seconds if args.max_seconds else None
    while not _stop:
        with closing(db.connect()) as conn:
            try:
                tick(conn)
            except Exception:
                log.exception("watcher pass failed")  # keep running; next pass retries
        if args.once or (deadline and time.monotonic() + LOOP_SLEEP >= deadline):
            break
        time.sleep(LOOP_SLEEP)
    lock.close()


if __name__ == "__main__":
    main()
