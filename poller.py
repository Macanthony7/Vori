#!/usr/bin/env python3
"""
Dedicated Poller - 10-second polling loop for crypto payment detection
Runs via cPanel cron every minute (* * * * *)
Internal loop: 30 iterations × 10 seconds = 5 minutes max execution
Routes balance checks to: Alchemy (ETH/ERC20), Ankr (BSC/BEP20), TronGrid (TRX/TRC20), BlockCypher (BTC/LTC/DOGE)
"""
import sys
import os
import time
import signal
import hmac
import hashlib
import requests
import json
from datetime import datetime, timedelta

# Cross-platform file locking
try:
    import fcntl
    HAS_FCNTL = True
except ImportError:
    HAS_FCNTL = False
    import msvcrt

sys.path.insert(0, os.path.dirname(__file__))

from db import execute_query
from config import (
    get_chain_config, get_webhook_config, get_app_key,
)
from rpc_client import (
    query_provider_balance, get_transaction_hash, verify_transaction_rpc
)
from utils import setup_logger, log_info, log_error, log_warning

logger = setup_logger('poller')

# ─── Configuration ────────────────────────────────────────────────────────────
POLL_INTERVAL_SECONDS = 10
MAX_LOOPS_PER_CRON = 30  # 30 × 10s = 5 minutes (cPanel limit)
SESSION_TIMEOUT_MINUTES = 15
# ──────────────────────────────────────────────────────────────────────────────

# Global flag for graceful shutdown
_shutdown = False
_lock_file = None

def signal_handler(signum, frame):
    global _shutdown
    log_warning(logger, f"Poller received signal {signum}, shutting down gracefully...")
    _shutdown = True

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)


def acquire_lock():
    """Acquire file lock to prevent concurrent poller instances (cross-platform)"""
    global _lock_file
    try:
        _lock_file = open('/tmp/crypto_poller.lock', 'w')
        if HAS_FCNTL:
            import fcntl
            fcntl.flock(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            import msvcrt
            msvcrt.locking(_lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        return True
    except (IOError, OSError, BlockingIOError):
        return False


def release_lock():
    """Release file lock (cross-platform)"""
    global _lock_file
    if _lock_file:
        try:
            if HAS_FCNTL:
                import fcntl
                fcntl.flock(_lock_file, fcntl.LOCK_UN)
            else:
                import msvcrt
                msvcrt.locking(_lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            _lock_file.close()
        except:
            pass
        _lock_file = None


def get_active_sessions():
    """
    Fetch all payment sessions that need polling:
    - status = 'pending'
    - created_at >= NOW() - 15 minutes
    - not expired (expires_at > NOW() OR expires_at IS NULL)
    """
    query = """
        SELECT id, transaction_id, user_id, coin, address_family, network,
               address_index, address, amount, amount_usd, status,
               expected_confirmations, confirmations, paid_amount,
               tx_hash, payment_meta, detected_at, confirmed_at,
               processed_at, expires_at, swept, sweep_tx_hash,
               swept_at, needs_sweep
        FROM crypto_payments
        WHERE status IN ('pending', 'confirming', 'confirmed')
          AND (expires_at IS NULL OR expires_at > NOW())
          AND created_at >= DATE_SUB(NOW(), INTERVAL %s MINUTE)
          AND (status IN ('pending', 'confirming') OR processed_at IS NULL)
        ORDER BY created_at ASC
    """
    results = execute_query(query, (SESSION_TIMEOUT_MINUTES,))
    return results or []


def update_session_status(session_id, status, **kwargs):
    """Update crypto_payments record with new status and optional fields"""
    fields = ['status = %s']
    values = [status]
    
    allowed_fields = {
        'confirmations', 'paid_amount', 'tx_hash', 'detected_at',
        'confirmed_at', 'processed_at', 'needs_sweep', 'swept',
        'sweep_tx_hash', 'swept_at', 'payment_meta'
    }
    
    for key, value in kwargs.items():
        if key in allowed_fields:
            fields.append(f"{key} = %s")
            values.append(value)
    
    values.append(session_id)
    
    query = f"UPDATE crypto_payments SET {', '.join(fields)} WHERE id = %s"
    execute_query(query, tuple(values))
    log_info(logger, f"[Session {session_id}] Status updated to {status}")


def call_laravel_webhook(action, transaction_id, payment_id=None):
    """
    Call Laravel webhook endpoint to finalize the deposit.
    The webhook will credit user balance, process referrals, etc.
    """
    webhook_config = get_webhook_config()
    webhook_url = webhook_config['url']
    timeout = webhook_config['timeout']
    
    if not webhook_url or 'yourdomain.com' in webhook_url:
        log_warning(logger, f"[Payment {payment_id}] Webhook URL not configured, skipping Laravel notification")
        return False
    
    # Generate HMAC signature for authentication
    app_key = get_app_key()
    payload = 'aegis-crypto-service'
    signature = hmac.new(
        app_key.encode() if app_key else b'',
        payload.encode(),
        hashlib.sha256
    ).hexdigest()
    
    # Prepare webhook payload
    webhook_data = {
        'transaction_id': transaction_id,
        'action': action
    }
    
    if payment_id:
        webhook_data['payment_id'] = payment_id
    
    headers = {
        'Content-Type': 'application/json',
        'X-Aegis-Auth': signature
    }
    
    try:
        log_info(logger, f"[Payment {payment_id}] Calling Laravel webhook: {action}")
        
        response = requests.post(
            webhook_url,
            json=webhook_data,
            headers=headers,
            timeout=timeout
        )
        
        if response.status_code == 200:
            result = response.json()
            if result.get('success'):
                log_info(logger, f"[Payment {payment_id}] Laravel webhook success: {result.get('message')}")
                return True
            else:
                log_error(logger, f"[Payment {payment_id}] Laravel webhook failed: {result.get('error')}")
                return False
        else:
            log_error(logger, f"[Payment {payment_id}] Laravel webhook HTTP {response.status_code}: {response.text}")
            return False
            
    except requests.exceptions.Timeout:
        log_error(logger, f"[Payment {payment_id}] Laravel webhook timeout ({timeout}s)")
        return False
    except requests.exceptions.RequestException as e:
        log_error(logger, f"[Payment {payment_id}] Laravel webhook request failed: {e}")
        return False
    except Exception as e:
        log_error(logger, f"[Payment {payment_id}] Laravel webhook unexpected error: {e}")
        return False


def retry_confirmed_webhook(session, transaction_id=None):
    """Retry the Laravel webhook for a confirmed payment whose webhook failed previously.
    Only called when status='confirmed' AND processed_at IS NULL."""
    session_id = session['id']
    
    if not transaction_id:
        transaction_id = session.get('transaction_id')
    
    log_info(logger, f"[Session {session_id}] Retrying webhook for confirmed payment (processed_at is NULL)")
    
    webhook_success = call_laravel_webhook('finalize_deposit', transaction_id, session_id)
    
    if webhook_success:
        # Mark as processed so we don't retry forever (Laravel webhook is idempotent)
        execute_query(
            "UPDATE crypto_payments SET processed_at = %s WHERE id = %s",
            (datetime.now(), session_id),
            fetch=False
        )
        log_info(logger, f"[Session {session_id}] Webhook retry succeeded, marked processed")
        return True
    else:
        log_warning(logger, f"[Session {session_id}] Webhook retry failed, will retry on next poll")
        return False


def process_confirming_session(session, chain, token_contract):
    """Check confirmations for a session already in 'confirming' status"""
    session_id = session['id']
    tx_hash = session.get('tx_hash')
    address = session.get('address', '')  # MUST pass real address, NOT ''
    expected_amount = float(session.get('amount', 0))
    expected_confirmations = session['expected_confirmations'] or get_chain_config(chain).get('confirmations', 1)
    
    if not tx_hash:
        log_warning(logger, f"[Session {session_id}] Confirming but no tx_hash found")
        return False
    
    log_info(logger, f"[Session {session_id}] Checking confirmations for {tx_hash}...")
    
    # Use verify_transaction_rpc to get current confirmations (non-blocking, single call)
    # Pass the REAL address and expected_amount so address/amount checks don't false-fail
    from rpc_client import verify_transaction_rpc
    result = verify_transaction_rpc(tx_hash, address, chain, expected_amount, token_contract)
    
    if result and result.get('valid'):
        confirmations = result.get('confirmations', 0)
        
        if confirmations >= expected_confirmations:
            # Confirmed! Credit user and mark for sweep
            log_info(logger, f"[Session {session_id}] CONFIRMED with {confirmations} confirmations")
            
            # Update status to confirmed
            update_session_status(session_id, 'confirmed',
                                confirmations=confirmations,
                                confirmed_at=datetime.now(),
                                needs_sweep=True)
            
            # Call Laravel webhook to credit user balance (independent of sweep)
            transaction_id = session['transaction_id']
            webhook_success = call_laravel_webhook('finalize_deposit', transaction_id, session_id)
            
            if webhook_success:
                # Mark webhook as delivered so we don't retry forever
                execute_query(
                    "UPDATE crypto_payments SET processed_at = %s WHERE id = %s",
                    (datetime.now(), session_id),
                    fetch=False
                )
                log_info(logger, f"[Session {session_id}] Laravel webhook completed successfully, marked processed")
            else:
                log_warning(logger, f"[Session {session_id}] Laravel webhook failed, will retry on next poll")
            
        else:
            # Still confirming - update confirmation count
            update_session_status(session_id, 'confirming', confirmations=confirmations)
            log_info(logger, f"[Session {session_id}] Confirming: {confirmations}/{expected_confirmations}")
    else:
        # Error checking - keep as confirming, will retry
        log_warning(logger, f"[Session {session_id}] Could not verify confirmations for {tx_hash}")
    
    return True


def process_session(session):
    """Process a single payment session"""
    session_id = session['id']
    chain = session['address_family'] or session['coin']
    address = session['address']
    expected_amount = float(session['amount'])
    token_contract = None
    status = session['status']
    
    # Check if this is a token payment
    payment_meta = session.get('payment_meta')
    if payment_meta and isinstance(payment_meta, dict):
        token_contract = payment_meta.get('token_contract')
    
    # Handle already-confirming sessions: check confirmations and progress to confirmed
    if status == 'confirming':
        return process_confirming_session(session, chain, token_contract)
    
    # Handle already-confirmed sessions whose webhook failed (retry the webhook)
    if status == 'confirmed':
        return retry_confirmed_webhook(session, transaction_id=session['transaction_id'])
    
    log_info(logger, f"[Session {session_id}] Polling {chain} address: {address}")
    
    try:
        # Check balance via provider-routed function
        balance = query_provider_balance(chain, address, token_contract)
        if balance is None:
            log_warning(logger, f"[Session {session_id}] Could not query balance from provider")
            return False
        
        log_info(logger, f"[Session {session_id}] {chain} balance: {balance} (expected: {expected_amount})")
        
        # Check if payment detected
        if balance >= expected_amount - 0.00000001:  # Allow tiny floating point diff
            # Payment detected! Find the transaction hash
            tx_hash = get_transaction_hash(chain, address, expected_amount, token_contract)
            if not tx_hash:
                log_warning(logger, f"[Session {session_id}] Payment detected but couldn't find transaction hash")
                return False
            
            log_info(logger, f"[Session {session_id}] Payment detected: {tx_hash}, marking as confirming...")
            
            # Update to confirming status - DON'T block on confirmations
            # Next cron cycle will check confirmations
            update_session_status(session_id, 'confirming', 
                                tx_hash=tx_hash, 
                                detected_at=datetime.now(),
                                paid_amount=balance)
            
            # Return True - session is now 'confirming', will be checked on next poll
            return True
            
        else:
            # No payment yet, check if session expired
            expires_at = session.get('expires_at')
            if expires_at and expires_at < datetime.now():
                log_info(logger, f"[Session {session_id}] Expired (no payment received)")
                update_session_status(session_id, 'expired')
            else:
                log_info(logger, f"[Session {session_id}] No payment yet (balance: {balance}/{expected_amount})")
        
        return True
        
    except Exception as e:
        log_error(logger, f"[Session {session_id}] Processing error: {e}")
        return False


def run_poll_cycle():
    """
    Single poll cycle - checks all active sessions once.
    Called repeatedly by the main loop.
    """
    global _shutdown
    
    if _shutdown:
        return False
    
    try:
        sessions = get_active_sessions()
        
        if not sessions:
            log_info(logger, "No active payment sessions to poll")
            return True
        
        log_info(logger, f"Polling {len(sessions)} active session(s)")
        
        for session in sessions:
            if _shutdown:
                break
            try:
                process_session(session)
            except Exception as e:
                log_error(logger, f"[Session {session['id']}] Processing error: {e}")
        
        return True
        
    except Exception as e:
        log_error(logger, f"Poll cycle failed: {e}")
        return True  # Continue running


def main():
    """Main entry point - runs 30 × 10s loops (max 5 minutes)"""
    # Acquire lock to prevent concurrent instances
    if not acquire_lock():
        log_warning(logger, "Another poller instance is running, exiting")
        return 0
    
    try:
        log_info(logger, "=" * 60)
        log_info(logger, f"POLLER STARTED - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        log_info(logger, f"Config: {MAX_LOOPS_PER_CRON} loops × {POLL_INTERVAL_SECONDS}s = {MAX_LOOPS_PER_CRON * POLL_INTERVAL_SECONDS}s max")
        log_info(logger, "=" * 60)
        
        start_time = time.time()
        loops_completed = 0
        
        for i in range(MAX_LOOPS_PER_CRON):
            if _shutdown:
                log_warning(logger, "Shutdown requested, stopping early")
                break
            
            loop_start = time.time()
            
            try:
                run_poll_cycle()
            except Exception as e:
                log_error(logger, f"Unexpected error in poll loop {i+1}: {e}")
            
            loops_completed += 1
            
            # Calculate remaining time in this cron window
            elapsed = time.time() - start_time
            if elapsed >= 290:  # Leave 10s buffer before 5min cron limit
                log_warning(logger, f"Approaching cron time limit ({elapsed:.0f}s), stopping")
                break
            
            # Sleep for poll interval (minus processing time)
            loop_elapsed = time.time() - loop_start
            sleep_time = max(0, POLL_INTERVAL_SECONDS - loop_elapsed)
            
            if sleep_time > 0:
                time.sleep(sleep_time)
        
        total_time = time.time() - start_time
        log_info(logger, f"Poller completed: {loops_completed} loops in {total_time:.1f}s")
        log_info(logger, "=" * 60)
        
        return 0
        
    finally:
        release_lock()


if __name__ == '__main__':
    sys.exit(main())