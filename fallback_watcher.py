#!/usr/bin/env python3
"""
Fallback Watcher - Polling Backup for Missed Webhooks
Runs every 5 minutes via cron to catch payments that webhooks missed.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from db import execute_query
from config import get_chain_config
from rpc_client import query_provider_balance, get_transaction_hash, verify_transaction_rpc
from poller import call_laravel_webhook
from utils import setup_logger, log_info, log_error, log_warning

logger = setup_logger('fallback_watcher')

# ─── Configuration ────────────────────────────────────────────────────────────
FALLBACK_WINDOW_MINUTES = 5  # Check payments older than this
# ──────────────────────────────────────────────────────────────────────────────


def get_stale_pending_payments():
    """
    Fetch payments that are pending/confirming and older than fallback window.
    These are candidates for missed webhooks.
    """
    query = """
        SELECT id, transaction_id, user_id, coin, address_family, network,
               address_index, address, amount, amount_usd, status,
               expected_confirmations, confirmations, paid_amount,
               tx_hash, payment_meta, detected_at, confirmed_at,
               processed_at, expires_at, swept, sweep_tx_hash,
               swept_at, needs_sweep
        FROM crypto_payments
        WHERE status IN ('pending', 'confirming')
          AND created_at <= DATE_SUB(NOW(), INTERVAL %s MINUTE)
          AND (expires_at IS NULL OR expires_at > NOW())
        ORDER BY created_at ASC
    """
    results = execute_query(query, (FALLBACK_WINDOW_MINUTES,))
    return results or []


def is_already_processed(tx_hash, chain):
    """Check if transaction was already processed (deduplication)"""
    if not tx_hash:
        return False
    result = execute_query(
        "SELECT 1 FROM processed_transactions WHERE tx_hash = %s AND chain = %s LIMIT 1",
        (tx_hash, chain)
    )
    return bool(result)


def mark_as_processed(tx_hash, chain, payment_id):
    """Record transaction as processed to prevent duplicate handling"""
    execute_query(
        "INSERT IGNORE INTO processed_transactions (tx_hash, chain, payment_id) VALUES (%s, %s, %s)",
        (tx_hash, chain, payment_id),
        fetch=False
    )


def process_payment(payment):
    """Process a single stale payment - verify on-chain and update status"""
    payment_id = payment['id']
    chain = payment['address_family'] or payment['coin']
    address = payment['address']
    expected_amount = float(payment['amount'])
    token_contract = payment.get('token_contract')

    log_info(logger, f"[Fallback] Checking payment #{payment_id}: {chain} {address}")

    # Verify transaction on-chain
    try:
        balance = query_provider_balance(chain, address, token_contract)
        if balance is None:
            log_warning(logger, f"[Fallback #{payment_id}] Could not query balance")
            return False
        
        if balance < expected_amount - 0.00000001:
            log_info(logger, f"[Fallback #{payment_id}] No payment yet (balance: {balance}, expected: {expected_amount})")
            return False
        
        # Payment detected! Find the transaction hash
        tx_hash = get_transaction_hash(chain, address, expected_amount, token_contract)
        if not tx_hash:
            log_warning(logger, f"[Fallback #{payment_id}] Payment detected but couldn't find tx hash")
            return False
        
        # Deduplication check
        if is_already_processed(tx_hash, chain):
            log_info(logger, f"[Fallback #{payment_id}] Transaction {tx_hash} already processed")
            return False
        
        # Verify transaction details and get confirmations
        result = verify_transaction_rpc(tx_hash, address, chain, expected_amount, token_contract)
        if not result or not result.get('valid'):
            log_warning(logger, f"[Fallback #{payment_id}] Transaction verification failed: {result}")
            return False
        
        confirmations = result.get('confirmations', 0)
        required = payment['expected_confirmations'] or get_chain_config(chain).get('confirmations', 1)
        
        # Update payment record
        if confirmations >= required:
            # Confirmed - mark status, then call Laravel webhook to credit user
            execute_query(
                "UPDATE crypto_payments SET status = 'confirmed', confirmations = %s, paid_amount = %s, tx_hash = %s, confirmed_at = NOW(), needs_sweep = 1 WHERE id = %s",
                (confirmations, balance, tx_hash, payment_id),
                fetch=False
            )
            # Use Laravel webhook to credit user (consistent with poller; handles referrals, idempotency)
            transaction_id = payment['transaction_id']
            webhook_success = call_laravel_webhook('finalize_deposit', transaction_id, payment_id)
            if webhook_success:
                execute_query(
                    "UPDATE crypto_payments SET processed_at = NOW() WHERE id = %s",
                    (payment_id,),
                    fetch=False
                )
                log_info(logger, f"[Fallback #{payment_id}] CONFIRMED - Webhook delivered, user credited via Laravel")
            else:
                log_warning(logger, f"[Fallback #{payment_id}] CONFIRMED but webhook failed - will be retried by poller")
        else:
            # Still confirming
            execute_query(
                "UPDATE crypto_payments SET status = 'confirming', confirmations = %s, paid_amount = %s, tx_hash = %s WHERE id = %s",
                (confirmations, balance, tx_hash, payment_id),
                fetch=False
            )
            log_info(logger, f"[Fallback #{payment_id}] CONFIRMING - {confirmations}/{required} confirmations")
        
        # Mark as processed in dedup table
        mark_as_processed(tx_hash, chain, payment_id)
        
        return True
        
    except Exception as e:
        log_error(logger, f"[Fallback #{payment_id}] Error processing payment: {e}")
        return False


def run_fallback_scan():
    """Main entry point - scan for stale payments and process them"""
    log_info(logger, "=== Starting fallback scan ===")
    
    payments = get_stale_pending_payments()
    
    if not payments:
        log_info(logger, "No stale pending payments found")
        return 0
    
    log_info(logger, f"Found {len(payments)} stale pending payment(s) to check")
    
    processed = 0
    for payment in payments:
        try:
            if process_payment(payment):
                processed += 1
        except Exception as e:
            log_error(logger, f"[Fallback #{payment['id']}] Unhandled error: {e}")
    
    log_info(logger, f"=== Fallback scan complete. Processed {processed}/{len(payments)} payments ===")
    return processed


if __name__ == '__main__':
    run_fallback_scan()