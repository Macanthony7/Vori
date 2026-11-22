#!/usr/bin/env python3
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from db import execute_query
from config import get_main_wallet, get_chain_config, SEED_PHRASE
from utils import setup_logger, log_info, log_error, log_warning
from datetime import datetime, timezone
from rpc_client import check_address_balance
from tx_builder import derive_private_key, execute_sweep

logger = setup_logger('sweeper')

def check_gas_balance(address, chain):
    """Check if address has enough gas for sweep"""
    balance = check_address_balance(address, chain)
    
    if balance is None:
        log_warning(logger, f"[{chain}] Could not check gas balance for {address}, assuming sufficient")
        return True  # Assume OK if we can't check
    
    # Minimum gas thresholds
    min_gas = {
        'ETH': 0.001,   # ~$3 at $3000/ETH
        'BSC': 0.002,   # ~$1 at $500/BNB
        'TRX': 10       # ~$2 at $0.20/TRX
    }
    
    required = min_gas.get(chain, 0)
    has_enough = balance >= required
    
    log_info(logger, f"[{chain}] Gas balance for {address}: {balance} (required: {required})")
    return has_enough

def sweep_deposit(deposit):
    """Sweep a single confirmed deposit"""
    deposit_id = deposit['id']
    chain = deposit['address_family']
    address = deposit['address']
    amount = float(deposit['paid_amount'] or deposit['amount'])
    index = deposit['address_index']
    token_contract = deposit.get('token_contract')
    
    if token_contract:
        log_info(logger, f"Sweeping deposit #{deposit_id}: {amount} tokens from {address} (contract: {token_contract})")
    else:
        log_info(logger, f"Sweeping deposit #{deposit_id}: {amount} {chain} from {address}")
    
    try:
        # Get main wallet address
        main_wallet = get_main_wallet(chain)
        if not main_wallet:
            log_error(logger, f"Deposit #{deposit_id}: No main wallet configured for {chain}")
            return False
        
        # Check if gas is needed (for EVM and TRON chains)
        chain_config = get_chain_config(chain)
        if chain_config.get('gas_token'):
            has_gas = check_gas_balance(address, chain)
            if not has_gas:
                log_warning(logger, f"Deposit #{deposit_id}: Insufficient gas. Marking for gas funding.")
                execute_query(
                    "UPDATE crypto_payments SET needs_sweep = 0 WHERE id = %s",
                    (deposit_id,),
                    fetch=False
                )
                # TODO: Queue gas funding request
                return False
        
        # Derive private key
        private_key = derive_private_key(chain, index)
        
        # Execute sweep transaction (new API: execute_sweep(from, to, chain, pk, token))
        tx_hash = execute_sweep(address, main_wallet, chain, private_key, token_contract)
        
        if tx_hash:
            # Update deposit record - set status='completed' after sweep succeeds
            execute_query(
                "UPDATE crypto_payments SET swept = 1, sweep_tx_hash = %s, swept_at = %s, needs_sweep = 0, status = 'completed' WHERE id = %s",
                (tx_hash, datetime.now(timezone.utc), deposit_id),
                fetch=False
            )
            log_info(logger, f"Deposit #{deposit_id}: Sweep successful. TX: {tx_hash}. Status now 'completed'.")
            return True
        else:
            log_error(logger, f"Deposit #{deposit_id}: Sweep transaction failed")
            return False
            
    except Exception as e:
        log_error(logger, f"Deposit #{deposit_id}: Error during sweep: {e}")
        return False

def process_sweeps():
    """Process all deposits that need sweeping"""
    log_info(logger, "=== Starting sweep processing ===")
    
    # Get deposits that need sweeping
    deposits = execute_query(
        """SELECT * FROM crypto_payments 
           WHERE status = 'confirmed' 
           AND swept = 0 
           AND needs_sweep = 1 
           ORDER BY confirmed_at ASC 
           LIMIT 50"""
    )
    
    if not deposits:
        log_info(logger, "No deposits need sweeping")
        return 0
    
    log_info(logger, f"Found {len(deposits)} deposits to sweep")
    
    swept_count = 0
    for deposit in deposits:
        if sweep_deposit(deposit):
            swept_count += 1
    
    log_info(logger, f"=== Sweep processing complete. Swept {swept_count}/{len(deposits)} deposits ===")
    return swept_count

if __name__ == '__main__':
    process_sweeps()