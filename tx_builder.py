#!/usr/bin/env python3
"""
Transaction builder for sweep operations.
Handles key derivation, transaction signing, and broadcasting.
Supports: BTC, ETH, BSC, LTC, DOGE, TRX (native + tokens)
"""
import requests
from config import (
    SEED_PHRASE, get_chain_config, get_main_wallet,
    ALCHEMY_API_KEY, ANKR_API_KEY, BLOCKCYPHER_API_KEY, TRONGRID_API_KEY
)
from utils import setup_logger, log_info, log_error, log_warning

logger = setup_logger('tx_builder')


# ─── Key Derivation ────────────────────────────────────────────────────────────

def derive_private_key(chain, index):
    """
    Derive private key from seed phrase using BIP44 derivation paths.
    
    BIP44 paths:
    - BTC:  m/44'/0'/0'/0/{index}
    - ETH:  m/44'/60'/0'/0/{index}
    - BSC:  m/44'/60'/0'/0/{index} (same as ETH)
    - LTC:  m/44'/2'/0'/0/{index}
    - DOGE: m/44'/3'/0'/0/{index}
    - TRX:  m/44'/195'/0'/0/{index}
    """
    if not SEED_PHRASE:
        log_error(logger, "AEGIS_SEED_PHRASE not configured")
        return None
    
    try:
        from bip_utils import Bip39SeedGenerator, Bip44, Bip44Coins, Bip44Changes
        
        # Generate seed from mnemonic
        seed_bytes = Bip39SeedGenerator(SEED_PHRASE).Generate()
        
        # Map chain to BIP44 coin type
        coin_map = {
            'BTC': Bip44Coins.BITCOIN,
            'ETH': Bip44Coins.ETHEREUM,
            'BSC': Bip44Coins.ETHEREUM,  # BSC uses same derivation as ETH
            'LTC': Bip44Coins.LITECOIN,
            'DOGE': Bip44Coins.DOGECOIN,
            'TRX': Bip44Coins.TRON,
        }
        
        coin_type = coin_map.get(chain.upper())
        if not coin_type:
            log_error(logger, f"Unsupported chain for key derivation: {chain}")
            return None
        
        # Derive key using BIP44: m/44'/coin_type'/0'/0/index
        bip44_mst = Bip44.FromSeed(seed_bytes, coin_type)
        bip44_acc = bip44_mst.Purpose().Coin().Account(0)
        bip44_chg = bip44_acc.Change(Bip44Changes.CHAIN_EXT)
        bip44_addr = bip44_chg.AddressIndex(index)
        
        # Get private key
        private_key = bip44_addr.PrivateKey().Raw().ToHex()
        
        log_info(logger, f"[{chain}] Derived private key for index {index}")
        return private_key
        
    except ImportError:
        log_error(logger, "bip_utils not installed. Run: pip install bip-utils")
        return None
    except Exception as e:
        log_error(logger, f"Error deriving private key: {e}")
        return None


# ─── UTXO Chains (BTC, LTC, DOGE) via BlockCypher ──────────────────────────────

def execute_utxo_sweep(from_address, to_address, chain, private_key):
    """Execute sweep transaction for UTXO chain (BTC/LTC/DOGE) via BlockCypher"""
    if not BLOCKCYPHER_API_KEY:
        log_error(logger, f"[{chain}] BlockCypher API key not configured")
        return None
    
    try:
        from bitcoinlib.keys import Key
        from bitcoinlib.transactions import Transaction
        
        chain_map = {'BTC': 'btc', 'LTC': 'ltc', 'DOGE': 'doge'}
        coin = chain_map.get(chain.upper())
        
        # Get UTXOs for address
        url = f"https://api.blockcypher.com/v1/{coin}/main/addrs/{from_address}"
        params = {'token': BLOCKCYPHER_API_KEY, 'unspentOnly': 'true'}
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        utxos = data.get('txrefs', [])
        if not utxos:
            log_error(logger, f"[{chain}] No UTXOs found for {from_address}")
            return None

        # Calculate total input amount
        total_input = sum(utxo['value'] for utxo in utxos)
        
        # Estimate fee (simplified - use 1 sat/byte * estimated tx size)
        # Rough estimate: 180 bytes per input + 34 bytes per output + 10 bytes overhead
        estimated_size = len(utxos) * 180 + 34 + 10
        fee = estimated_size  # 1 sat/byte
        
        output_amount = total_input - fee
        
        if output_amount <= 0:
            log_error(logger, f"[{chain}] Insufficient funds after fee: {total_input} - {fee}")
            return None
        
        # Create transaction using BlockCypher's transaction API
        tx_data = {
            'inputs': [{'addresses': [from_address]}],
            'outputs': [{'addresses': [to_address], 'value': output_amount}]
        }
        
        create_url = f"https://api.blockcypher.com/v1/{coin}/main/txs/new"
        params = {'token': BLOCKCYPHER_API_KEY}
        response = requests.post(create_url, json=tx_data, params=params, timeout=10)
        response.raise_for_status()
        tx_skeleton = response.json()
        
        # Sign transaction
        key = Key(private_key, compressed=True)
        
        # Sign each input
        signatures = []
        pubkeys = []
        for tosign in tx_skeleton.get('tosign', []):
            sig = key.sign(bytes.fromhex(tosign))
            signatures.append(sig.hex())
            pubkeys.append(key.public_hex)
        
        # Send signed transaction
        tx_skeleton['signatures'] = signatures
        tx_skeleton['pubkeys'] = pubkeys
        
        send_url = f"https://api.blockcypher.com/v1/{coin}/main/txs/send"
        response = requests.post(send_url, json=tx_skeleton, params=params, timeout=10)
        response.raise_for_status()
        result = response.json()
        
        tx_hash = result.get('tx', {}).get('hash')
        log_info(logger, f"[{chain}] Sweep transaction broadcast: {tx_hash}")
        return tx_hash
        
    except ImportError:
        log_error(logger, "bitcoinlib not installed. Run: pip install bitcoinlib")
        return None
    except Exception as e:
        log_error(logger, f"[{chain}] Error executing sweep: {e}")
        return None


def execute_btc_sweep(from_address, to_address, amount, chain, private_key):
    """Wrapper for BTC sweep"""
    return execute_utxo_sweep(from_address, to_address, chain, private_key)


def execute_ltc_sweep(from_address, to_address, amount, chain, private_key):
    """Wrapper for LTC sweep"""
    return execute_utxo_sweep(from_address, to_address, chain, private_key)


def execute_doge_sweep(from_address, to_address, amount, chain, private_key):
    """Wrapper for DOGE sweep"""
    return execute_utxo_sweep(from_address, to_address, chain, private_key)


# ─── EVM Chains (ETH, BSC) ─────────────────────────────────────────────────────

def execute_evm_native_sweep(from_address, to_address, chain, private_key):
    """Sweep native ETH/BNB via Alchemy/Ankr JSON-RPC"""
    if chain == 'ETH':
        if not ALCHEMY_API_KEY:
            log_error(logger, f"[{chain}] Alchemy API key not configured")
            return None
        url = f"https://eth-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"
    elif chain == 'BSC':
        if not ANKR_API_KEY:
            log_error(logger, f"[{chain}] Ankr API key not configured")
            return None
        url = f"https://rpc.ankr.com/bsc/{ANKR_API_KEY}"
    else:
        log_error(logger, f"Unsupported EVM chain: {chain}")
        return None
    
    try:
        from web3 import Web3
        from eth_account import Account
        
        w3 = Web3(Web3.HTTPProvider(url))
        
        if not w3.is_connected():
            log_error(logger, f"[{chain}] Could not connect to node")
            return None
        
        account = Account.from_key(private_key)
        
        # Get balance
        balance_wei = w3.eth.get_balance(from_address)
        balance_eth = balance_wei / 1e18
        
        if balance_wei == 0:
            log_error(logger, f"[{chain}] No native balance to sweep")
            return None
        
        # Estimate gas
        gas_price = w3.eth.gas_price
        gas_limit = 21000  # Standard transfer
        fee_wei = gas_price * gas_limit
        fee_eth = fee_wei / 1e18
        
        if balance_wei <= fee_wei:
            log_error(logger, f"[{chain}] Insufficient balance for gas: {balance_eth} < {fee_eth}")
            return None
        
        # Calculate output amount (balance - fee)
        output_wei = balance_wei - fee_wei
        
        # Build transaction
        nonce = w3.eth.get_transaction_count(from_address)
        
        transaction = {
            'from': from_address,
            'to': Web3.to_checksum_address(to_address),
            'value': output_wei,
            'gas': gas_limit,
            'gasPrice': gas_price,
            'nonce': nonce,
            'chainId': 1 if chain == 'ETH' else 56
        }
        
        # Sign and send
        signed_txn = account.sign_transaction(transaction)
        tx_hash = w3.eth.send_raw_transaction(signed_txn.rawTransaction)
        tx_hash_hex = tx_hash.hex()
        
        log_info(logger, f"[{chain}] Native sweep broadcast: {tx_hash_hex} ({output_wei/1e18} {chain})")
        return tx_hash_hex
        
    except Exception as e:
        log_error(logger, f"[{chain}] Error executing native sweep: {e}")
        return None


def execute_evm_token_sweep(from_address, to_address, token_contract, chain, private_key):
    """Sweep ERC20/BEP20 token via Alchemy/Ankr JSON-RPC"""
    if chain == 'ETH':
        if not ALCHEMY_API_KEY:
            log_error(logger, f"[{chain}] Alchemy API key not configured")
            return None
        url = f"https://eth-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"
        chain_id = 1
    elif chain == 'BSC':
        if not ANKR_API_KEY:
            log_error(logger, f"[{chain}] Ankr API key not configured")
            return None
        url = f"https://rpc.ankr.com/bsc/{ANKR_API_KEY}"
        chain_id = 56
    else:
        log_error(logger, f"Unsupported EVM chain for token: {chain}")
        return None
    
    # Validate token contract is whitelisted
    from config import is_valid_token_contract
    if not is_valid_token_contract(chain, token_contract):
        log_error(logger, f"[{chain}] Token contract not whitelisted: {token_contract}")
        return None
    
    try:
        from web3 import Web3
        from eth_account import Account
        
        w3 = Web3(Web3.HTTPProvider(url))
        
        if not w3.is_connected():
            log_error(logger, f"[{chain}] Could not connect to node")
            return None
        
        account = Account.from_key(private_key)
        
        # ERC20 contract ABI (transfer function only)
        erc20_abi = [
            {
                "constant": False,
                "inputs": [
                    {"name": "_to", "type": "address"},
                    {"name": "_value", "type": "uint256"}
                ],
                "name": "transfer",
                "outputs": [{"name": "", "type": "bool"}],
                "type": "function"
            },
            {
                "constant": True,
                "inputs": [{"name": "_owner", "type": "address"}],
                "name": "balanceOf",
                "outputs": [{"name": "balance", "type": "uint256"}],
                "type": "function"
            }
        ]
        
        contract = w3.eth.contract(address=Web3.to_checksum_address(token_contract), abi=erc20_abi)
        
        # Get token balance
        token_balance = contract.functions.balanceOf(from_address).call()
        
        if token_balance == 0:
            log_error(logger, f"[{chain}] No token balance to sweep")
            return None
        
        # Check native gas balance
        native_balance = w3.eth.get_balance(from_address)
        gas_price = w3.eth.gas_price
        
        # Estimate gas for token transfer
        gas_estimate = contract.functions.transfer(
            Web3.to_checksum_address(to_address), token_balance
        ).estimate_gas({'from': from_address})
        
        gas_limit = gas_estimate + 5000  # Buffer
        fee_wei = gas_price * gas_limit
        
        if native_balance < fee_wei:
            log_error(logger, f"[{chain}] Insufficient native balance for gas: {native_balance/1e18} < {fee_wei/1e18}")
            return None
        
        # Build transaction
        nonce = w3.eth.get_transaction_count(from_address)
        
        transaction = contract.functions.transfer(
            Web3.to_checksum_address(to_address), token_balance
        ).build_transaction({
            'from': from_address,
            'gas': gas_limit,
            'gasPrice': gas_price,
            'nonce': nonce,
            'chainId': chain_id
        })
        
        # Sign and send
        signed_txn = account.sign_transaction(transaction)
        tx_hash = w3.eth.send_raw_transaction(signed_txn.rawTransaction)
        tx_hash_hex = tx_hash.hex()
        
        log_info(logger, f"[{chain}] Token sweep broadcast: {tx_hash_hex}")
        return tx_hash_hex
        
    except Exception as e:
        log_error(logger, f"[{chain}] Error executing token sweep: {e}")
        return None


def execute_eth_sweep(from_address, to_address, amount, chain, private_key):
    """Wrapper for ETH native sweep"""
    return execute_evm_native_sweep(from_address, to_address, chain, private_key)


def execute_bsc_sweep(from_address, to_address, amount, chain, private_key):
    """Wrapper for BSC native sweep"""
    return execute_evm_native_sweep(from_address, to_address, chain, private_key)


def execute_eth_token_sweep(from_address, to_address, token_contract, chain, private_key):
    """Wrapper for ETH token sweep"""
    return execute_evm_token_sweep(from_address, to_address, token_contract, chain, private_key)


def execute_bsc_token_sweep(from_address, to_address, token_contract, chain, private_key):
    """Wrapper for BSC token sweep"""
    return execute_evm_token_sweep(from_address, to_address, token_contract, chain, private_key)


# ─── TRON (TRX) ────────────────────────────────────────────────────────────────

def execute_trx_native_sweep(from_address, to_address, chain, private_key):
    """Sweep native TRX via TronGrid"""
    if not TRONGRID_API_KEY:
        log_error(logger, f"[{chain}] TronGrid API key not configured")
        return None
    
    try:
        from tronpy import Tron
        from tronpy.keys import PrivateKey
        
        client = Tron(network='mainnet', provider=f"https://api.trongrid.io")
        priv_key = PrivateKey(bytes.fromhex(private_key))
        
        # Get balance
        account = client.get_account(from_address)
        balance_sun = account.get('balance', 0)
        balance_trx = balance_sun / 1e6
        
        if balance_sun == 0:
            log_error(logger, f"[{chain}] No TRX balance to sweep")
            return None
        
        # Estimate fee for TRX transfer (~250,000 SUN = 0.25 TRX)
        fee_estimate = 2_000_000  # 2 TRX in SUN (conservative)
        
        if balance_sun <= fee_estimate:
            log_error(logger, f"[{chain}] Insufficient TRX for fee: {balance_trx} < {fee_estimate/1e6}")
            return None
        
        output_sun = balance_sun - fee_estimate
        
        # Build transaction
        txn = (
            client.trx.transfer(from_address, to_address, output_sun)
            .memo("sweep")
            .build()
            .sign(priv_key)
        )
        
        # Broadcast
        result = txn.broadcast()
        tx_hash = result.get('txid') or result.get('transaction', {}).get('txID')
        
        if tx_hash:
            log_info(logger, f"[{chain}] TRX sweep broadcast: {tx_hash} ({output_sun/1e6} TRX)")
            return tx_hash
        else:
            log_error(logger, f"[{chain}] Failed to broadcast TRX sweep: {result}")
            return None
            
    except ImportError:
        log_error(logger, "tronpy not installed. Run: pip install tronpy")
        return None
    except Exception as e:
        log_error(logger, f"[{chain}] Error executing TRX sweep: {e}")
        return None


def execute_trx_token_sweep(from_address, to_address, token_contract, chain, private_key):
    """Sweep TRC20 token via TronGrid"""
    if not TRONGRID_API_KEY:
        log_error(logger, f"[{chain}] TronGrid API key not configured")
        return None
    
    # Validate token contract is whitelisted
    from config import is_valid_token_contract
    if not is_valid_token_contract(chain, token_contract):
        log_error(logger, f"[{chain}] Token contract not whitelisted: {token_contract}")
        return None
    
    try:
        from tronpy import Tron
        from tronpy.keys import PrivateKey
        from tronpy.contract import Contract
        
        client = Tron(network='mainnet', provider=f"https://api.trongrid.io")
        priv_key = PrivateKey(bytes.fromhex(private_key))
        
        # Get contract
        contract = client.get_contract(token_contract)
        
        # Get token balance
        token_balance = contract.functions.balanceOf(from_address)
        
        if token_balance == 0:
            log_error(logger, f"[{chain}] No token balance to sweep")
            return None
        
        # Check native TRX for fees
        account = client.get_account(from_address)
        trx_balance = account.get('balance', 0)
        
        # Estimate energy for TRC20 transfer (~32,000 energy + bandwidth)
        # Energy price ~ 420 SUN per unit (variable)
        energy_needed = 32000
        energy_price = 420  # SUN per energy unit
        fee_estimate = energy_needed * energy_price + 1_000_000  # + bandwidth
        
        if trx_balance < fee_estimate:
            log_error(logger, f"[{chain}] Insufficient TRX for token transfer fee: {trx_balance/1e6} < {fee_estimate/1e6}")
            return None
        
        # Build and sign transaction
        txn = (
            contract.functions.transfer(to_address, token_balance)
            .with_owner(from_address)
            .fee_limit(fee_estimate)
            .build()
            .sign(priv_key)
        )
        
        result = txn.broadcast()
        tx_hash = result.get('txid') or result.get('transaction', {}).get('txID')
        
        if tx_hash:
            log_info(logger, f"[{chain}] TRC20 sweep broadcast: {tx_hash} ({token_balance/1e6} tokens)")
            return tx_hash
        else:
            log_error(logger, f"[{chain}] Failed to broadcast TRC20 sweep: {result}")
            return None
            
    except ImportError:
        log_error(logger, "tronpy not installed. Run: pip install tronpy")
        return None
    except Exception as e:
        log_error(logger, f"[{chain}] Error executing TRC20 sweep: {e}")
        return None


def execute_trx_sweep(from_address, to_address, amount, chain, private_key):
    """Wrapper for TRX native sweep"""
    return execute_trx_native_sweep(from_address, to_address, chain, private_key)


def execute_trx_token_sweep_wrapper(from_address, to_address, token_contract, chain, private_key):
    """Wrapper for TRX token sweep"""
    return execute_trx_token_sweep(from_address, to_address, token_contract, chain, private_key)


# ─── Main Router ──────────────────────────────────────────────────────────────

def execute_sweep(from_address, to_address, chain, private_key, token_contract=None):
    """
    Main entry point for sweep operations.
    Routes to chain-specific implementation.
    """
    chain = chain.upper()
    
    if chain in ['BTC', 'LTC', 'DOGE']:
        return execute_utxo_sweep(from_address, to_address, chain, private_key)
    elif chain == 'ETH':
        if token_contract:
            return execute_evm_token_sweep(from_address, to_address, token_contract, chain, private_key)
        return execute_evm_native_sweep(from_address, to_address, chain, private_key)
    elif chain == 'BSC':
        if token_contract:
            return execute_evm_token_sweep(from_address, to_address, token_contract, chain, private_key)
        return execute_evm_native_sweep(from_address, to_address, chain, private_key)
    elif chain == 'TRX':
        if token_contract:
            return execute_trx_token_sweep(from_address, to_address, token_contract, chain, private_key)
        return execute_trx_native_sweep(from_address, to_address, chain, private_key)
    else:
        log_error(logger, f"Unsupported chain for sweep: {chain}")
        return None


# ─── CLI Test ──────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import sys
    if len(sys.argv) >= 5:
        from_addr = sys.argv[1]
        to_addr = sys.argv[2]
        chain = sys.argv[3]
        idx = int(sys.argv[4])
        token = sys.argv[5] if len(sys.argv) > 5 else None
        
        pk = derive_private_key(chain, idx)
        if pk:
            result = execute_sweep(from_addr, to_addr, chain, pk, token)
            print(f"Sweep result: {result}")
        else:
            print("Failed to derive private key")
    else:
        print("Usage: python tx_builder.py <from_addr> <to_addr> <chain> <index> [token_contract]")