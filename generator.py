#!/usr/bin/env python3
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from db import execute_query, execute_many
from config import SEED_PHRASE, get_chain_config
from utils import setup_logger, log_info, log_error
from bip_utils import (
    Bip39SeedGenerator, Bip44, Bip44Coins, Bip44Changes,
    TrxAddrEncoder, TrxAddr
)

logger = setup_logger('generator')

# Pool thresholds
MIN_POOL_SIZE = 200
MAX_POOL_SIZE = 2000
REFILL_BATCH_SIZE = 100

CHAIN_MAP = {
    'BTC': Bip44Coins.BITCOIN,
    'ETH': Bip44Coins.ETHEREUM,
    'BSC': Bip44Coins.ETHEREUM,  # BSC uses same derivation as ETH
    'LTC': Bip44Coins.LITECOIN,
    'DOGE': Bip44Coins.DOGECOIN,
    'TRX': Bip44Coins.TRON,
}

def generate_address(chain, index):
    """Generate a single address for given chain and index"""
    # Validate chain parameter type and format
    if not isinstance(chain, str) or not chain.isalnum() or len(chain) > 10:
        raise ValueError(f"Invalid chain parameter: {chain}")
    if chain not in CHAIN_MAP:
        raise ValueError(f"Unsupported chain: {chain}")
    if not isinstance(index, int) or index < 0 or index > 2**32 - 1:
        raise ValueError(f"Invalid index: {index}. Must be an integer between 0 and 2^32-1")
    
    seed_bytes = Bip39SeedGenerator(SEED_PHRASE).Generate()
    bip44_ctx = Bip44.FromSeed(seed_bytes, CHAIN_MAP[chain])
    bip44_acc_ctx = bip44_ctx.Purpose().Coin().Account(0)
    bip44_chg_ctx = bip44_acc_ctx.Change(Bip44Changes.CHAIN_EXT)
    bip44_addr_ctx = bip44_chg_ctx.AddressIndex(index)
    
    if chain == 'TRX':
        pub_key_bytes = bip44_addr_ctx.PublicKey().RawCompressed().ToBytes()
        address = TrxAddr.EncodeKey(pub_key_bytes)
    else:
        address = bip44_addr_ctx.PublicKey().ToAddress()
    
    return address

def get_pool_status(chain):
    """Get current pool status for a chain"""
    if chain not in CHAIN_MAP:
        raise ValueError(f"Unsupported chain: {chain}")
    result = execute_query(
        "SELECT COUNT(*) as count FROM address_pool WHERE chain = %s AND status = 'unused'",
        (chain,)
    )
    return result[0]['count'] if result else 0

def get_max_index(chain):
    """Get the highest index used for a chain"""
    if chain not in CHAIN_MAP:
        raise ValueError(f"Unsupported chain: {chain}")
    result = execute_query(
        "SELECT MAX(`index`) as max_index FROM address_pool WHERE chain = %s",
        (chain,)
    )
    return result[0]['max_index'] if result and result[0]['max_index'] is not None else -1

def get_existing_addresses():
    """Get all existing addresses in the pool as a set for fast lookup."""
    results = execute_query("SELECT address FROM address_pool")
    return {row['address'] for row in results} if results else set()


def generate_batch(chain, start_index, count):
    """Generate a batch of addresses"""
    if chain not in CHAIN_MAP:
        raise ValueError(f"Unsupported chain: {chain}")
    if not isinstance(start_index, int) or start_index < 0:
        raise ValueError(f"Invalid start_index: {start_index}. Must be a non-negative integer")
    if not isinstance(count, int) or count <= 0 or count > REFILL_BATCH_SIZE:
        raise ValueError(f"Invalid count: {count}. Must be between 1 and {REFILL_BATCH_SIZE}")
    addresses = []
    for i in range(count):
        index = start_index + i
        try:
            address = generate_address(chain, index)
            addresses.append((chain, address, index, 'unused'))
            log_info(logger, f"Generated {chain} address at index {index}: {address}")
        except Exception as e:
            log_error(logger, f"Failed to generate {chain} address at index {index}: {e}")
    
    return addresses

def refill_pool(chain):
    """Refill address pool for a specific chain"""
    unused_count = get_pool_status(chain)
    
    log_info(logger, f"[{chain}] Current unused pool size: {unused_count}")
    
    # Check if we're at max capacity
    if unused_count >= MAX_POOL_SIZE:
        log_info(logger, f"[{chain}] Pool at maximum capacity ({MAX_POOL_SIZE}). Skipping generation.")
        return 0
    
    # Check if refill is needed
    if unused_count > MIN_POOL_SIZE:
        log_info(logger, f"[{chain}] Pool above minimum threshold ({MIN_POOL_SIZE}). No refill needed.")
        return 0
    
def refill_pool(chain):
    """Refill address pool for a specific chain"""
    unused_count = get_pool_status(chain)
    
    log_info(logger, f"[{chain}] Current unused pool size: {unused_count}")
    
    # Check if we're at max capacity
    if unused_count >= MAX_POOL_SIZE:
        log_info(logger, f"[{chain}] Pool at maximum capacity ({MAX_POOL_SIZE}). Skipping generation.")
        return 0
    
    # Check if refill is needed
    if unused_count > MIN_POOL_SIZE:
        log_info(logger, f"[{chain}] Pool above minimum threshold ({MIN_POOL_SIZE}). No refill needed.")
        return 0
    
    # Calculate how many to generate
    target_count = MAX_POOL_SIZE - unused_count
    max_index = get_max_index(chain)
    start_index = max_index + 1
    
    # Load all existing addresses to skip duplicates (e.g., ETH/BSC collision)
    existing_addresses = get_existing_addresses()
    
    log_info(logger, f"[{chain}] Refilling pool: generating {target_count} addresses starting from index {start_index}")
    
    # Generate in batches, skipping any addresses already in the pool for ANY chain
    total_generated = 0
    while total_generated < target_count:
        batch_size = min(REFILL_BATCH_SIZE, target_count - total_generated)
        batch = generate_batch(chain, start_index, batch_size)
        
        if batch:
            # Filter out addresses that already exist (e.g., from ETH when filling BSC)
            new_batch = [item for item in batch if item[1] not in existing_addresses]
            
            if new_batch:
                # Add new addresses to existing set to prevent future duplicates in this run
                for item in new_batch:
                    existing_addresses.add(item[1])
                
                execute_many(
                    "INSERT INTO address_pool (chain, address, `index`, status) VALUES (%s, %s, %s, %s)",
                    new_batch
                )
                total_generated += len(new_batch)
                log_info(logger, f"[{chain}] Inserted batch of {len(new_batch)} addresses (skipped {len(batch) - len(new_batch)} duplicates). Total: {total_generated}/{target_count}")
            else:
                log_info(logger, f"[{chain}] All {len(batch)} addresses in batch were duplicates. Continuing to next batch...")
            
            # Advance to the next batch regardless
            start_index += batch_size
        else:
            log_error(logger, f"[{chain}] Failed to generate batch starting at index {start_index}")
            break
    
    log_info(logger, f"[{chain}] Pool refill complete. Generated {total_generated} new addresses.")
    return total_generated

def check_and_refill_all_chains():
    """Check and refill pools for all supported chains"""
    chains = ['BTC', 'ETH', 'BSC', 'LTC', 'DOGE', 'TRX']
    
    log_info(logger, "=== Starting address pool check ===")
    
    total_generated = 0
    for chain in chains:
        try:
            generated = refill_pool(chain)
            total_generated += generated
        except Exception as e:
            log_error(logger, f"Error refilling pool for {chain}: {e}")
    
    log_info(logger, f"=== Pool check complete. Total addresses generated: {total_generated} ===")
    return total_generated

if __name__ == '__main__':
    if len(sys.argv) == 3:
        # Legacy mode: generate single address
        chain = sys.argv[1].upper() if isinstance(sys.argv[1], str) else ''
        if not chain or not chain.isalnum() or len(chain) > 10:
            print(f"Error: Invalid chain parameter", file=sys.stderr)
            sys.exit(1)
        try:
            index = int(sys.argv[2])
        except (ValueError, TypeError):
            print(f"Error: Invalid index parameter", file=sys.stderr)
            sys.exit(1)
        try:
            address = generate_address(chain, index)
            print(address)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        # Pool management mode
        check_and_refill_all_chains()
