#!/usr/bin/env python3
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from db import execute_query
from utils import setup_logger, log_info, log_error
from datetime import datetime, timezone
from tx_builder import derive_private_key, execute_sweep
from config import get_main_wallet, SEED_PHRASE
from rpc_client import check_address_balance

logger = setup_logger('manual_actions')

def _mark(action_id, status, tx_hash=None, error=None):
    """Mark an action with final status"""
    execute_query(
        "UPDATE manual_actions_log SET status = %s, tx_hash = %s, error_message = %s WHERE id = %s",
        (status, tx_hash, error, action_id),
        fetch=False
    )

def process_fund_gas(action):
    """
    Fund gas to a deposit address.
    Sends native token (ETH/BNB/TRX) from main wallet to the deposit address.
    """
    chain = action['chain']
    to_address = action.get('address_to') or action.get('address_from')
    if not to_address:
        log_error(logger, f"Action #{action['id']}: No target address specified")
        _mark(action['id'], 'failed', error='No target address')
        return False
    amount = float(action.get('amount', 0))
    
    if not SEED_PHRASE:
        log_error(logger, f"Action #{action['id']}: AEGIS_SEED_PHRASE not configured")
        _mark(action['id'], 'failed', error='Seed phrase not configured')
        return False
    
    # Get main wallet address
    main_wallet = get_main_wallet(chain)
    if not main_wallet:
        log_error(logger, f"Action #{action['id']}: No main wallet configured for {chain}")
        _mark(action['id'], 'failed', error=f'No main wallet for {chain}')
        return False
    
    # Check main wallet has enough balance
    main_balance = check_address_balance(main_wallet, chain)
    if main_balance is None:
        log_error(logger, f"Action #{action['id']}: Could not check main wallet balance")
        _mark(action['id'], 'failed', error='Could not check main wallet balance')
        return False
    
    if main_balance < amount:
        log_error(logger, f"Action #{action['id']}: Insufficient main wallet balance: {main_balance} < {amount}")
        _mark(action['id'], 'failed', error=f'Insufficient balance: {main_balance}')
        return False
    
    # Derive main wallet private key (index 0 is reserved for main wallet)
    # Note: In production, main wallet key should be stored separately, not derived
    log_info(logger, f"Action #{action['id']}: Funding {amount} {chain} gas to {to_address}")
    
    # For now, we'll use a simplified approach:
    # The main wallet should be managed separately with its own key
    # This is a security consideration - main wallet shouldn't use same seed as deposit addresses
    log_error(logger, f"Action #{action['id']}: fund_gas requires main wallet private key management")
    _mark(action['id'], 'failed', error='Main wallet key management not implemented - use external wallet')
    return False
    
    # TODO: Implement proper main wallet key management
    # Option 1: Store main wallet private key encrypted in database
    # Option 2: Use hardware wallet / HSM for main wallet
    # Option 3: Manual gas funding via external wallet (current workaround)

def process_retry_sweep(action):
    """
    Re-queue the deposit for sweeping by resetting needs_sweep.
    The sweeper will pick it up on the next cron cycle.
    """
    result = execute_query(
        "SELECT id FROM crypto_payments WHERE address = %s AND address_family = %s LIMIT 1",
        (action['address_from'], action['chain'])
    )
    if not result:
        _mark(action['id'], 'failed', error='Deposit record not found')
        return False
    
    execute_query(
        "UPDATE crypto_payments SET needs_sweep = 1, swept = 0 WHERE id = %s",
        (result[0]['id'],),
        fetch=False
    )
    _mark(action['id'], 'success')
    log_info(logger, f"Action #{action['id']}: retry_sweep queued for deposit #{result[0]['id']}")
    return True

def process_manual_send(action):
    """
    Manually send transaction from deposit address.
    Signs and broadcasts a transaction from address_from to address_to.
    Used when automatic sweep fails or for emergency fund recovery.
    """
    chain = action['chain']
    from_address = action['address_from']
    to_address = action['address_to']
    amount = float(action.get('amount', 0))
    
    if not SEED_PHRASE:
        log_error(logger, f"Action #{action['id']}: AEGIS_SEED_PHRASE not configured")
        _mark(action['id'], 'failed', error='Seed phrase not configured')
        return False
    
    # Get the deposit record to find the address index and token_contract
    result = execute_query(
        "SELECT id, address_index, token_contract FROM crypto_payments WHERE address = %s AND address_family = %s LIMIT 1",
        (from_address, chain)
    )
    
    if not result:
        log_error(logger, f"Action #{action['id']}: Deposit record not found for {from_address}")
        _mark(action['id'], 'failed', error='Deposit record not found')
        return False
    
    deposit = result[0]
    address_index = deposit['address_index']
    token_contract = deposit.get('token_contract')
    
    # Derive private key
    private_key = derive_private_key(chain, address_index)
    if not private_key:
        log_error(logger, f"Action #{action['id']}: Could not derive private key")
        _mark(action['id'], 'failed', error='Key derivation failed')
        return False
    
    # Check balance
    balance = check_address_balance(from_address, chain)
    if balance is None:
        log_error(logger, f"Action #{action['id']}: Could not check address balance")
        _mark(action['id'], 'failed', error='Could not check balance')
        return False
    
    if balance <= 0:
        log_error(logger, f"Action #{action['id']}: No balance to send: {balance}")
        _mark(action['id'], 'failed', error=f'No balance: {balance}')
        return False
    
    # Execute transaction
    log_info(logger, f"Action #{action['id']}: Sending {amount} {chain} from {from_address} to {to_address}")
    
    try:
        tx_hash = execute_sweep(from_address, to_address, chain, private_key, token_contract)
        
        if tx_hash:
            _mark(action['id'], 'success', tx_hash=tx_hash)
            log_info(logger, f"Action #{action['id']}: Manual send successful. TX: {tx_hash}")
            
            # Optionally mark the deposit as swept if sending to main wallet
            main_wallet = get_main_wallet(chain)
            if to_address.lower() == main_wallet.lower():
                execute_query(
                    "UPDATE crypto_payments SET swept = 1, sweep_tx_hash = %s, swept_at = %s, status = 'completed' WHERE id = %s",
                    (tx_hash, datetime.now(timezone.utc), deposit['id']),
                    fetch=False
                )
                log_info(logger, f"Action #{action['id']}: Marked deposit #{deposit['id']} as swept")
            
            return True
        else:
            log_error(logger, f"Action #{action['id']}: Transaction execution failed")
            _mark(action['id'], 'failed', error='Transaction execution failed')
            return False
            
    except Exception as e:
        log_error(logger, f"Action #{action['id']}: Error executing transaction: {e}")
        _mark(action['id'], 'failed', error=str(e))
        return False

def process_pending_actions():
    """Process all pending manual actions"""
    log_info(logger, "=== Starting manual actions processing ===")
    
    actions = execute_query(
        "SELECT * FROM manual_actions_log WHERE status = 'pending' ORDER BY created_at ASC LIMIT 20"
    )
    
    if not actions:
        log_info(logger, "No pending manual actions")
        return 0
    
    log_info(logger, f"Found {len(actions)} pending manual actions")
    
    handlers = {
        'fund_gas':    process_fund_gas,
        'retry_sweep': process_retry_sweep,
        'send':        process_manual_send,
    }
    
    done = 0
    for action in actions:
        handler = handlers.get(action['action_type'])
        if not handler:
            _mark(action['id'], 'failed', error=f"Unknown action type: {action['action_type']}")
            continue
        try:
            if handler(action):
                done += 1
        except Exception as e:
            log_error(logger, f"Action #{action['id']}: unhandled error: {e}")
            _mark(action['id'], 'failed', error=str(e))
    
    log_info(logger, f"=== Manual actions complete. Processed {done}/{len(actions)} ===")
    return done

if __name__ == '__main__':
    process_pending_actions()
