#!/usr/bin/env python3
"""
RPC client for blockchain verification.
Implements per-chain transaction and balance verification.
Supports native coins AND tokens (ERC20/BEP20/TRC20).
"""
import requests
from config import (
    ALCHEMY_API_KEY, BLOCKCYPHER_API_KEY, ANKR_API_KEY, TRONGRID_API_KEY,
    get_chain_config, get_alchemy_eth_url, is_eth_testnet
)
from utils import setup_logger, log_info, log_error, log_warning

logger = setup_logger('rpc_client')

# ============================================================================
# BLOCKCYPHER (BTC, LTC, DOGE)
# ============================================================================

def verify_btc_transaction(tx_hash, address, chain, expected_amount):
    """Verify BTC/LTC/DOGE transaction via BlockCypher API"""
    if not BLOCKCYPHER_API_KEY:
        log_warning(logger, f"[{chain}] BlockCypher API key not configured")
        return None
    
    # Validate chain to prevent SSRF
    chain_map = {
        'BTC': 'btc',
        'LTC': 'ltc',
        'DOGE': 'doge'
    }
    
    coin = chain_map.get(chain.upper())
    if not coin:
        log_error(logger, f"[{chain}] Unsupported chain for BlockCypher")
        return None
    
    # Additional SSRF protection: ensure coin is in whitelist
    if coin not in ['btc', 'ltc', 'doge']:
        log_error(logger, f"[{chain}] Invalid coin value for URL construction")
        return None
    
    # Validate tx_hash format (hex string)
    if not isinstance(tx_hash, str) or not all(c in '0123456789abcdefABCDEF' for c in tx_hash):
        log_error(logger, f"[{chain}] Invalid transaction hash format")
        return {'valid': False, 'error': 'Invalid transaction hash'}
    
    try:
        # Use only whitelisted API endpoint
        url = f"https://api.blockcypher.com/v1/{coin}/main/txs/{tx_hash}"
        params = {'token': BLOCKCYPHER_API_KEY}
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if 'error' in data:
            log_error(logger, f"[{chain}] Transaction not found: {tx_hash}")
            return {'valid': False, 'error': 'Transaction not found'}
        
        confirmations = data.get('confirmations', 0)
        
        amount_received = 0
        address_found = False
        
        for output in data.get('outputs', []):
            if address in output.get('addresses', []):
                address_found = True
                amount_received += output.get('value', 0) / 100000000
        
        if not address_found:
            log_warning(logger, f"[{chain}] Address {address} not found in transaction outputs")
            return {'valid': False, 'error': 'Address not in transaction'}
        
        log_info(logger, f"[{chain}] Verified TX {tx_hash}: {amount_received} {chain}, {confirmations} confirmations")
        
        return {
            'valid': True,
            'confirmations': confirmations,
            'amount': amount_received,
            'tx_hash': tx_hash
        }
        
    except requests.exceptions.RequestException as e:
        log_error(logger, f"[{chain}] BlockCypher API error: {e}")
        return None
    except Exception as e:
        log_error(logger, f"[{chain}] Unexpected error verifying transaction: {e}")
        return None


# ============================================================================
# ALCHEMY (ETH)
# ============================================================================

def verify_eth_transaction(tx_hash, address, chain, expected_amount, token_contract=None):
    """Verify Ethereum transaction via Alchemy API (native ETH or ERC20 tokens)"""
    if not ALCHEMY_API_KEY:
        log_warning(logger, f"[{chain}] Alchemy API key not configured")
        return None
    
    # Validate tx_hash format to prevent SSRF
    if not isinstance(tx_hash, str) or len(tx_hash) != 66 or not tx_hash.startswith('0x'):
        log_error(logger, f"[{chain}] Invalid transaction hash format")
        return {'valid': False, 'error': 'Invalid transaction hash'}
    
    if not all(c in '0123456789abcdefABCDEF' for c in tx_hash[2:]):
        log_error(logger, f"[{chain}] Invalid transaction hash characters")
        return {'valid': False, 'error': 'Invalid transaction hash'}
    
    # Validate token_contract format if provided
    if token_contract:
        if not isinstance(token_contract, str) or len(token_contract) != 42 or not token_contract.startswith('0x'):
            log_error(logger, f"[{chain}] Invalid token contract format")
            return {'valid': False, 'error': 'Invalid token contract'}
    
    try:
        # Use only whitelisted API endpoint
        url = get_alchemy_eth_url()
        if is_eth_testnet():
            log_info(logger, f"[{chain}] Using ETH Sepolia testnet endpoint")
        
        receipt_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_getTransactionReceipt",
            "params": [tx_hash]
        }
        
        response = requests.post(url, json=receipt_payload, timeout=10)
        response.raise_for_status()
        receipt_data = response.json()
        
        if 'error' in receipt_data:
            log_error(logger, f"[{chain}] Error getting receipt: {receipt_data['error']}")
            return {'valid': False, 'error': receipt_data['error'].get('message', 'Unknown error')}
        
        receipt = receipt_data.get('result')
        if not receipt:
            log_warning(logger, f"[{chain}] Transaction not found or not mined: {tx_hash}")
            return {'valid': False, 'error': 'Transaction not found'}
        
        if receipt.get('status') != '0x1':
            log_error(logger, f"[{chain}] Transaction failed on-chain: {tx_hash}")
            return {'valid': False, 'error': 'Transaction failed'}
        
        block_payload = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "eth_blockNumber",
            "params": []
        }
        
        response = requests.post(url, json=block_payload, timeout=10)
        response.raise_for_status()
        block_data = response.json()
        
        current_block = int(block_data.get('result', '0x0'), 16)
        tx_block = int(receipt.get('blockNumber', '0x0'), 16)
        confirmations = max(0, current_block - tx_block + 1)
        
        # ERC20 Token Transfer
        if token_contract:
            token_contract = token_contract.lower()
            transfer_topic = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
            
            amount_tokens = 0
            address_found = False
            
            for log in receipt.get('logs', []):
                if log.get('address', '').lower() != token_contract:
                    continue
                    
                topics = log.get('topics', [])
                if len(topics) < 3 or topics[0] != transfer_topic:
                    continue
                
                to_address_padded = topics[2]
                to_address_extracted = '0x' + to_address_padded[-40:]
                
                if to_address_extracted.lower() == address.lower():
                    address_found = True
                    amount_hex = log.get('data', '0x0')
                    amount_wei = int(amount_hex, 16)
                    amount_tokens += amount_wei / 1e6  # USDT/USDC use 6 decimals
            
            if not address_found:
                log_warning(logger, f"[{chain}] Token transfer to {address} not found in logs")
                return {'valid': False, 'error': 'Address not in token transfer'}
            
            log_info(logger, f"[{chain}] Verified ERC20 TX {tx_hash}: {amount_tokens} tokens, {confirmations} confirmations")
            
            return {
                'valid': True,
                'confirmations': confirmations,
                'amount': amount_tokens,
                'tx_hash': tx_hash
            }
        
        # Native ETH Transfer
        else:
            tx_payload = {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "eth_getTransactionByHash",
                "params": [tx_hash]
            }
            
            response = requests.post(url, json=tx_payload, timeout=10)
            response.raise_for_status()
            tx_data = response.json()
            
            tx = tx_data.get('result')
            if not tx:
                log_error(logger, f"[{chain}] Could not fetch transaction details")
                return {'valid': False, 'error': 'Could not fetch transaction'}
            
            to_address = tx.get('to', '').lower()
            if to_address != address.lower():
                log_warning(logger, f"[{chain}] Address mismatch: expected {address}, got {to_address}")
                return {'valid': False, 'error': 'Address mismatch'}
            
            value_wei = int(tx.get('value', '0x0'), 16)
            amount_eth = value_wei / 1e18
            
            log_info(logger, f"[{chain}] Verified ETH TX {tx_hash}: {amount_eth} ETH, {confirmations} confirmations")
            
            return {
                'valid': True,
                'confirmations': confirmations,
                'amount': amount_eth,
                'tx_hash': tx_hash
            }
        
    except requests.exceptions.RequestException as e:
        log_error(logger, f"[{chain}] Alchemy API error: {e}")
        return None
    except Exception as e:
        log_error(logger, f"[{chain}] Unexpected error verifying transaction: {e}")
        return None


# ============================================================================
# ANKR (BSC)
# ============================================================================

def verify_bsc_transaction(tx_hash, address, chain, expected_amount, token_contract=None):
    """Verify BSC transaction via Ankr API (native BNB or BEP20 tokens)"""
    if not ANKR_API_KEY:
        log_warning(logger, f"[{chain}] Ankr API key not configured")
        return None
    
    # Validate tx_hash format to prevent SSRF
    if not isinstance(tx_hash, str) or len(tx_hash) != 66 or not tx_hash.startswith('0x'):
        log_error(logger, f"[{chain}] Invalid transaction hash format")
        return {'valid': False, 'error': 'Invalid transaction hash'}
    
    if not all(c in '0123456789abcdefABCDEF' for c in tx_hash[2:]):
        log_error(logger, f"[{chain}] Invalid transaction hash characters")
        return {'valid': False, 'error': 'Invalid transaction hash'}
    
    # Validate token_contract format if provided
    if token_contract:
        if not isinstance(token_contract, str) or len(token_contract) != 42 or not token_contract.startswith('0x'):
            log_error(logger, f"[{chain}] Invalid token contract format")
            return {'valid': False, 'error': 'Invalid token contract'}
    
    try:
        # Use only whitelisted API endpoint
        url = f"https://rpc.ankr.com/bsc/{ANKR_API_KEY}"
        
        receipt_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_getTransactionReceipt",
            "params": [tx_hash]
        }
        
        response = requests.post(url, json=receipt_payload, timeout=10)
        response.raise_for_status()
        receipt_data = response.json()
        
        if 'error' in receipt_data:
            log_error(logger, f"[{chain}] Error getting receipt: {receipt_data['error']}")
            return {'valid': False, 'error': receipt_data['error'].get('message', 'Unknown error')}
        
        receipt = receipt_data.get('result')
        if not receipt:
            log_warning(logger, f"[{chain}] Transaction not found or not mined: {tx_hash}")
            return {'valid': False, 'error': 'Transaction not found'}
        
        if receipt.get('status') != '0x1':
            log_error(logger, f"[{chain}] Transaction failed on-chain: {tx_hash}")
            return {'valid': False, 'error': 'Transaction failed'}
        
        block_payload = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "eth_blockNumber",
            "params": []
        }
        
        response = requests.post(url, json=block_payload, timeout=10)
        response.raise_for_status()
        block_data = response.json()
        
        current_block = int(block_data.get('result', '0x0'), 16)
        tx_block = int(receipt.get('blockNumber', '0x0'), 16)
        confirmations = max(0, current_block - tx_block + 1)
        
        # BEP20 Token Transfer
        if token_contract:
            token_contract = token_contract.lower()
            transfer_topic = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
            
            amount_tokens = 0
            address_found = False
            
            for log in receipt.get('logs', []):
                if log.get('address', '').lower() != token_contract:
                    continue
                    
                topics = log.get('topics', [])
                if len(topics) < 3 or topics[0] != transfer_topic:
                    continue
                
                to_address_padded = topics[2]
                to_address_extracted = '0x' + to_address_padded[-40:]
                
                if to_address_extracted.lower() == address.lower():
                    address_found = True
                    amount_hex = log.get('data', '0x0')
                    amount_wei = int(amount_hex, 16)
                    amount_tokens += amount_wei / 1e6  # USDT/USDC use 6 decimals
            
            if not address_found:
                log_warning(logger, f"[{chain}] Token transfer to {address} not found in logs")
                return {'valid': False, 'error': 'Address not in token transfer'}
            
            log_info(logger, f"[{chain}] Verified BEP20 TX {tx_hash}: {amount_tokens} tokens, {confirmations} confirmations")
            
            return {
                'valid': True,
                'confirmations': confirmations,
                'amount': amount_tokens,
                'tx_hash': tx_hash
            }
        
        # Native BNB Transfer
        else:
            tx_payload = {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "eth_getTransactionByHash",
                "params": [tx_hash]
            }
            
            response = requests.post(url, json=tx_payload, timeout=10)
            response.raise_for_status()
            tx_data = response.json()
            
            tx = tx_data.get('result')
            if not tx:
                log_error(logger, f"[{chain}] Could not fetch transaction details")
                return {'valid': False, 'error': 'Could not fetch transaction'}
            
            to_address = tx.get('to', '').lower()
            if to_address != address.lower():
                log_warning(logger, f"[{chain}] Address mismatch: expected {address}, got {to_address}")
                return {'valid': False, 'error': 'Address mismatch'}
            
            value_wei = int(tx.get('value', '0x0'), 16)
            amount_bnb = value_wei / 1e18
            
            log_info(logger, f"[{chain}] Verified BNB TX {tx_hash}: {amount_bnb} BNB, {confirmations} confirmations")
            
            return {
                'valid': True,
                'confirmations': confirmations,
                'amount': amount_bnb,
                'tx_hash': tx_hash
            }
        
    except requests.exceptions.RequestException as e:
        log_error(logger, f"[{chain}] Ankr API error: {e}")
        return None
    except Exception as e:
        log_error(logger, f"[{chain}] Unexpected error verifying transaction: {e}")
        return None


# ============================================================================
# TRONGRID (TRX)
# ============================================================================

def verify_trx_transaction(tx_hash, address, chain, expected_amount, token_contract=None):
    """Verify TRON transaction via TronGrid API (native TRX or TRC20 tokens)"""
    if not TRONGRID_API_KEY:
        log_warning(logger, f"[{chain}] TronGrid API key not configured")
        return None
    
    # Validate tx_hash format to prevent SSRF (TRON uses 64 hex chars)
    if not isinstance(tx_hash, str) or len(tx_hash) != 64:
        log_error(logger, f"[{chain}] Invalid transaction hash format")
        return {'valid': False, 'error': 'Invalid transaction hash'}
    
    if not all(c in '0123456789abcdefABCDEF' for c in tx_hash):
        log_error(logger, f"[{chain}] Invalid transaction hash characters")
        return {'valid': False, 'error': 'Invalid transaction hash'}
    
    # Validate token_contract format if provided (TRON base58 address)
    if token_contract:
        if not isinstance(token_contract, str) or not token_contract.startswith('T'):
            log_error(logger, f"[{chain}] Invalid token contract format")
            return {'valid': False, 'error': 'Invalid token contract'}
    
    try:
        # Use only whitelisted API endpoint
        url = f"https://api.trongrid.io/wallet/gettransactionbyid"
        headers = {'TRON-PRO-API-KEY': TRONGRID_API_KEY}
        params = {'value': tx_hash}
        
        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if 'Error' in data or not data:
            log_error(logger, f"[{chain}] Transaction not found: {tx_hash}")
            return {'valid': False, 'error': 'Transaction not found'}
        
        ret = data.get('ret', [{}])[0]
        if ret.get('contractRet') != 'SUCCESS':
            log_error(logger, f"[{chain}] Transaction failed: {ret.get('contractRet')}")
            return {'valid': False, 'error': 'Transaction failed'}
        
        raw_data = data.get('raw_data', {})
        contract = raw_data.get('contract', [{}])[0]
        contract_type = contract.get('type')
        parameter = contract.get('parameter', {}).get('value', {})
        
        block_url = "https://api.trongrid.io/wallet/getnowblock"
        block_response = requests.get(block_url, headers=headers, timeout=10)
        block_response.raise_for_status()
        block_data = block_response.json()
        
        current_block = block_data.get('block_header', {}).get('raw_data', {}).get('number', 0)
        tx_block = data.get('blockNumber', 0)
        confirmations = max(0, current_block - tx_block + 1) if tx_block else 0
        
        # TRC20 Token Transfer
        if token_contract:
            if contract_type != 'TriggerSmartContract':
                log_error(logger, f"[{chain}] Not a smart contract transaction")
                return {'valid': False, 'error': 'Not a token transfer'}
            
            contract_address = parameter.get('contract_address')
            if not contract_address:
                log_error(logger, f"[{chain}] No contract address in transaction")
                return {'valid': False, 'error': 'No contract address'}
            
            try:
                from tronpy.keys import to_base58check_address
            except ImportError:
                log_error(logger, "tronpy not installed. Run: pip install tronpy")
                return {'valid': False, 'error': 'tronpy library required for TRC20 verification'}
            
            contract_address_base58 = to_base58check_address('41' + contract_address)
            
            if contract_address_base58 != token_contract:
                log_error(logger, f"[{chain}] Contract mismatch: expected {token_contract}, got {contract_address_base58}")
                return {'valid': False, 'error': 'Wrong token contract'}
            
            data_hex = parameter.get('data', '')
            if len(data_hex) < 136:
                log_error(logger, f"[{chain}] Invalid transfer data")
                return {'valid': False, 'error': 'Invalid transfer data'}
            
            to_address_hex = data_hex[32:72]
            to_address_base58 = to_base58check_address('41' + to_address_hex[-40:])
            
            if to_address_base58 != address:
                log_warning(logger, f"[{chain}] Address mismatch: expected {address}, got {to_address_base58}")
                return {'valid': False, 'error': 'Address mismatch'}
            
            amount_hex = data_hex[72:136]
            amount_sun = int(amount_hex, 16)
            amount_tokens = amount_sun / 1e6
            
            log_info(logger, f"[{chain}] Verified TRC20 TX {tx_hash}: {amount_tokens} tokens, {confirmations} confirmations")
            
            return {
                'valid': True,
                'confirmations': confirmations,
                'amount': amount_tokens,
                'tx_hash': tx_hash
            }
        
        # Native TRX Transfer
        else:
            if contract_type != 'TransferContract':
                log_error(logger, f"[{chain}] Not a TRX transfer transaction")
                return {'valid': False, 'error': 'Not a TRX transfer'}
            
            to_address_hex = parameter.get('to_address')
            if not to_address_hex:
                log_error(logger, f"[{chain}] No recipient address in transaction")
                return {'valid': False, 'error': 'No recipient address'}
            
            try:
                from tronpy.keys import to_base58check_address
            except ImportError:
                log_error(logger, "tronpy not installed. Run: pip install tronpy")
                return {'valid': False, 'error': 'tronpy library required for TRX verification'}
            
            to_address_base58 = to_base58check_address('41' + to_address_hex)
            
            if to_address_base58 != address:
                log_warning(logger, f"[{chain}] Address mismatch: expected {address}, got {to_address_base58}")
                return {'valid': False, 'error': 'Address mismatch'}
            
            amount_sun = parameter.get('amount', 0)
            amount_trx = amount_sun / 1e6
            
            log_info(logger, f"[{chain}] Verified TRX TX {tx_hash}: {amount_trx} TRX, {confirmations} confirmations")
            
            return {
                'valid': True,
                'confirmations': confirmations,
                'amount': amount_trx,
                'tx_hash': tx_hash
            }
        
    except requests.exceptions.RequestException as e:
        log_error(logger, f"[{chain}] TronGrid API error: {e}")
        return None
    except Exception as e:
        log_error(logger, f"[{chain}] Unexpected error: {e}")
        return None


# ============================================================================
# MAIN VERIFICATION ROUTER
# ============================================================================

def verify_transaction_rpc(tx_hash, address, chain, expected_amount, token_contract=None):
    """
    Verify transaction via RPC based on chain type.
    Supports both native coins and tokens (ERC20/BEP20/TRC20).
    Returns dict with 'valid', 'confirmations', 'amount' or None if API unavailable.
    """
    chain = chain.upper()
    
    if chain in ['BTC', 'LTC', 'DOGE']:
        return verify_btc_transaction(tx_hash, address, chain, expected_amount)
    elif chain == 'ETH':
        return verify_eth_transaction(tx_hash, address, chain, expected_amount, token_contract)
    elif chain == 'BSC':
        return verify_bsc_transaction(tx_hash, address, chain, expected_amount, token_contract)
    elif chain == 'TRX':
        return verify_trx_transaction(tx_hash, address, chain, expected_amount, token_contract)
    else:
        log_error(logger, f"Unsupported chain: {chain}")
        return None


def check_address_balance(address, chain):
    """
    Check balance of an address (for gas checking).
    Returns balance in native token or None if unavailable.
    """
    return query_provider_balance(chain, address, None)


# ─── Balance Query (for fallback watcher & poller) ─────────────────────────────

def query_provider_balance(chain, address, token_contract=None):
    """
    Query balance for address (native or token).
    Returns float balance or None on error.
    Used by fallback watcher to detect payments.
    """
    chain = chain.upper()
    
    try:
        if chain in ['BTC', 'LTC', 'DOGE']:
            # UTXO chains - use BlockCypher
            if not BLOCKCYPHER_API_KEY:
                return None
            chain_map = {'BTC': 'btc', 'LTC': 'ltc', 'DOGE': 'doge'}
            coin = chain_map.get(chain)
            if not coin:
                return None
            url = f"https://api.blockcypher.com/v1/{coin}/main/addrs/{address}"
            params = {'token': BLOCKCYPHER_API_KEY}
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            # BlockCypher returns balance in satoshis
            return data.get('balance', 0) / 1e8
            
        elif chain in ['ETH', 'BSC']:
            # EVM chains - native balance
            if not token_contract:
                if chain == 'ETH' and not ALCHEMY_API_KEY:
                    return None
                if chain == 'BSC' and not ANKR_API_KEY:
                    return None
                url = get_alchemy_eth_url() if chain == 'ETH' else f"https://rpc.ankr.com/bsc/{ANKR_API_KEY}"
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "eth_getBalance",
                    "params": [address, "latest"]
                }
                response = requests.post(url, json=payload, timeout=10)
                response.raise_for_status()
                data = response.json()
                balance_wei = int(data.get('result', '0x0'), 16)
                return balance_wei / 1e18
            else:
                # Token balance via eth_call to balanceOf
                if chain == 'ETH' and not ALCHEMY_API_KEY:
                    return None
                if chain == 'BSC' and not ANKR_API_KEY:
                    return None
                url = get_alchemy_eth_url() if chain == 'ETH' else f"https://rpc.ankr.com/bsc/{ANKR_API_KEY}"
                # balanceOf(address) = 0x70a08231 + address (padded to 32 bytes)
                addr_padded = address.lower().replace('0x', '').zfill(64)
                data = '0x70a08231' + addr_padded
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "eth_call",
                    "params": [{"to": token_contract, "data": data}, "latest"]
                }
                response = requests.post(url, json=payload, timeout=10)
                response.raise_for_status()
                result = response.json()
                if 'result' in result and result['result'] != '0x':
                    balance_wei = int(result['result'], 16)
                    # Assume 6 decimals for USDT/USDC, 18 for others
                    return balance_wei / 1e6
                return 0
            
        elif chain == 'TRX':
            # TRON - native or TRC20
            if not TRONGRID_API_KEY:
                return None
            headers = {'TRON-PRO-API-KEY': TRONGRID_API_KEY}
            if not token_contract:
                # Native TRX
                url = "https://api.trongrid.io/wallet/getaccount"
                payload = {"address": address, "visible": True}
                response = requests.post(url, headers=headers, json=payload, timeout=10)
                response.raise_for_status()
                data = response.json()
                return data.get('balance', 0) / 1e6
            else:
                # TRC20 token - use triggerconstantcontract for balanceOf
                url = "https://api.trongrid.io/wallet/triggerconstantcontract"
                # balanceOf(address) = 0x70a08231 + address (32 bytes)
                addr_padded = address.lower().replace('0x', '').zfill(64) if address.startswith('0x') else address
                # TRON addresses are base58, convert to hex
                try:
                    from tronpy.keys import from_base58check_address
                    addr_hex = from_base58check_address(address)[2:].zfill(64)
                except:
                    addr_hex = address.replace('0x', '').zfill(64)
                data = '0x70a08231' + addr_padded
                payload = {
                    "owner_address": address,
                    "contract_address": token_contract,
                    "function_selector": "balanceOf(address)",
                    "parameter": data,
                    "visible": True
                }
                response = requests.post(url, headers=headers, json=payload, timeout=10)
                response.raise_for_status()
                result = response.json()
                if result.get('result', {}).get('result') == True:
                    constant_result = result.get('constant_result', ['0x0'])[0]
                    return int(constant_result, 16) / 1e6
                return 0
            
        else:
            log_warning(logger, f"Balance check not implemented for {chain}")
            return None
            
    except Exception as e:
        log_error(logger, f"[{chain}] Error checking balance for {address}: {e}")
        return None


# ─── Helper: ETH/ERC20 Transaction Hash via Alchemy ───────────────────────────

def _get_transaction_hash_eth_alchemy(address, expected_amount, token_contract=None):
    """
    Find transaction hash for ETH/ERC20 deposits using Alchemy's alchemy_getAssetTransfers.
    Returns tx hash if found matching expected amount, else None.
    """
    if not ALCHEMY_API_KEY:
        log_warning(logger, "[ETH] Alchemy API key not configured")
        return None
    
    try:
        # Determine category based on token type
        if token_contract:
            category = ["erc20"]
        else:
            category = ["external"]
        
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "alchemy_getAssetTransfers",
            "params": [{
                "fromBlock": "0x0",
                "toBlock": "latest",
                "toAddress": address,
                "category": category,
                "withMetadata": False,
                "excludeZeroValue": True,
                "maxCount": "0x3e8",  # 1000
                "order": "desc"
            }]
        }
        
        # Add contract address filter if token
        if token_contract:
            payload["params"][0]["contractAddresses"] = [token_contract.lower()]
        
        response = requests.post(
            get_alchemy_eth_url(),
            json=payload,
            timeout=15
        )
        response.raise_for_status()
        data = response.json()
        
        if 'result' not in data or 'transfers' not in data['result']:
            log_warning(logger, f"[ETH] No transfers found for address {address}")
            return None
        
        # Alchemy returns value differently depending on transfer category:
        # - external (native ETH): value is a DECIMAL STRING in ETH units (e.g. "2.6")
        # - erc20 (tokens): value is a DECIMAL STRING in the token's smallest unit (raw integer)
        # CRITICAL BUG PREVIOUSLY: code did int(float("0.0026")) = 0, so no fractional
        # ETH payment could ever match. We must compare in the SAME unit the API returns.

        for transfer in data['result']['transfers']:
            value_raw = transfer.get('value', '0')
            try:
                # Normalize to float for comparison (handles "0x..", "123", "123.45", 123.45)
                value_str = str(value_raw)
                if value_str.startswith('0x'):
                    value = int(value_str, 16)
                else:
                    value = float(value_str)
            except (ValueError, TypeError):
                continue

            tx_hash = transfer.get('hash')
            if not tx_hash:
                continue

            if token_contract:
                # ERC20: value is in raw token units (smallest unit, e.g. 6 decimals for USDT)
                # expected_amount is in token units, so compare in raw units (×1e6)
                expected_raw = expected_amount * 1e6
                min_raw = expected_raw * 0.99  # Allow 1% variance
                if value >= min_raw:
                    log_info(logger, f"[ETH] Found matching ERC20 tx: {tx_hash} for {expected_amount}")
                    return tx_hash
            else:
                # Native ETH: value is in ETH units (decimal, e.g. 2.6)
                # expected_amount is also in ETH units, compare directly with 1% variance
                min_eth = expected_amount * 0.99
                if value >= min_eth:
                    log_info(logger, f"[ETH] Found matching native tx: {tx_hash} for {expected_amount}")
                    return tx_hash

        log_warning(logger, f"[ETH] No matching transaction found for {address} amount {expected_amount}")
        return None
        
    except requests.exceptions.RequestException as e:
        log_error(logger, f"[ETH] Alchemy request failed: {e}")
        return None
    except Exception as e:
        log_error(logger, f"[ETH] Error finding transaction hash: {e}")
        return None


def _get_transaction_hash_bsc_ankr(address, expected_amount, token_contract=None):
    """
    Find transaction hash for BSC/BEP20 deposits using Ankr's eth_getLogs.
    For tokens: uses eth_getLogs with Transfer event filter.
    For native BNB: scans recent blocks (simplified - in production use BscScan API).
    """
    if not ANKR_API_KEY:
        log_warning(logger, "[BSC] Ankr API key not configured")
        return None
    
    if not token_contract:
        # Native BNB - Ankr eth_getLogs cannot scan native transfers (no event logs for simple sends)
        # The fallback_watcher + balance-check path still detects these payments; only the
        # tx_hash lookup is unavailable here. To enable this, use BscScan API or an indexer.
        log_warning(logger, f"[BSC] Native BNB tx hash lookup not implemented for {address} - will be caught by balance detection; consider adding BscScan API for tx hash")
        return None
    
    try:
        url = f"https://rpc.ankr.com/bsc/{ANKR_API_KEY}"
        
        # ERC20 Transfer event signature: Transfer(address,address,uint256)
        # topic0 = keccak256("Transfer(address,address,uint256)")
        transfer_topic = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
        
        # Encode address as topic (32 bytes, right-padded)
        address_topic = "0x" + address.lower().replace('0x', '').rjust(64, '0')
        
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_getLogs",
            "params": [{
                "fromBlock": "0x0",
                "toBlock": "latest",
                "address": token_contract.lower(),
                "topics": [transfer_topic, None, "0x" + address.lower().replace('0x', '').rjust(64, '0')]
            }]
        }
        
        response = requests.post(
            f"https://rpc.ankr.com/bsc/{ANKR_API_KEY}",
            json=payload,
            timeout=15
        )
        response.raise_for_status()
        data = response.json()
        
        if 'result' not in data:
            return None
        
        # Sort logs by block number descending (most recent first)
        logs = sorted(data['result'], key=lambda x: int(x.get('blockNumber', '0x0'), 16), reverse=True)

        # BEP20 tokens: raw value is in the token's smallest unit.
        # USDT/USDC use 6 decimals. expected_amount is in token units.
        # Compare against expected × 1e6 (same assumption as balance check in query_provider_balance).
        expected_raw = expected_amount * 1e6
        min_raw = expected_raw * 0.99  # Allow 1% variance

        for log in logs:
            # data field contains the amount (uint256) as a hex string
            data_hex = log.get('data', '0x0')
            try:
                value = int(data_hex, 16)
            except (ValueError, TypeError):
                continue

            if value >= min_raw:
                tx_hash = log.get('transactionHash')
                if tx_hash:
                    log_info(logger, f"[BSC] Found matching BEP20 tx: {tx_hash} for {expected_amount}")
                    return tx_hash

        return None
        
    except requests.exceptions.RequestException as e:
        log_error(logger, f"[BSC] Ankr request failed: {e}")
        return None
    except Exception as e:
        log_error(logger, f"[BSC] Error finding transaction hash: {e}")
        return None


def _get_transaction_hash_trx_trongrid(address, expected_amount, token_contract=None):
    """
    Find transaction hash for TRX/TRC20 deposits using TronGrid v2 API.
    """
    if not TRONGRID_API_KEY:
        log_warning(logger, "[TRX] TronGrid API key not configured")
        return None
    
    try:
        headers = {'TRON-PRO-API-KEY': TRONGRID_API_KEY}
        
        if token_contract:
            # TRC20 token - use /v2/accounts/{address}/transactions/trc20
            url = f"https://api.trongrid.io/v2/accounts/{address}/transactions/trc20"
            params = {
                'contract_address': token_contract,
                'only_confirmed': 'true',
                'limit': 50,
                'order_by': 'block_timestamp,desc'
            }
        else:
            # Native TRX - use /v2/accounts/{address}/transactions
            url = f"https://api.trongrid.io/v2/accounts/{address}/transactions"
            params = {
                'only_confirmed': 'true',
                'limit': 50,
                'order_by': 'block_timestamp,desc'
            }
        
        response = requests.get(url, headers=headers, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        if 'data' not in data:
            log_warning(logger, f"[TRX] No transaction data for {address}")
            return None
        
        expected_sun = int(round(expected_amount * 1e6))
        min_amount = int(expected_sun * 0.99)  # Allow 1% variance (FE WERE NEVER USED)

        for tx in data['data']:
            if token_contract:
                # TRC20 transaction
                # Verify it's the correct contract FIRST (cheaper than value parse)
                if tx.get('token_info', {}).get('address', '').lower() != token_contract.lower():
                    continue
                # Verify the 'to' address matches our address (was missing before)
                tx_to = tx.get('to', '')
                if tx_to and tx_to.lower() != address.lower():
                    continue
                value_raw = tx.get('value', '0')
                try:
                    # Value is an integer string in the token's smallest unit (e.g. sun for USDT)
                    # Use str() coercion to safely handle string/number inputs
                    value_str = str(value_raw)
                    value = int(float(value_str))
                except (ValueError, TypeError):
                    continue
            else:
                # Native TRX transaction
                # Check if it's a transfer to our address
                tx_to = tx.get('to', '')
                if tx_to.lower() != address.lower():
                    continue
                value_raw = tx.get('value', '0')
                try:
                    # Value is an integer string in SUN (1 TRX = 1e6 sun)
                    value_str = str(value_raw)
                    value = int(float(value_str))
                except (ValueError, TypeError):
                    continue

            # Use the 1%-variance threshold (was comparing against strict expected_sun before)
            if value >= min_amount:
                tx_hash = tx.get('txID') or tx.get('transaction_id')
                if tx_hash:
                    log_info(logger, f"[TRX] Found matching tx: {tx_hash} for {expected_amount}")
                    return tx_hash

        log_warning(logger, f"[TRX] No matching transaction found for {address} amount {expected_amount}")
        return None
        
    except requests.exceptions.RequestException as e:
        log_error(logger, f"[TRX] TronGrid request failed: {e}")
        return None
    except Exception as e:
        log_error(logger, f"[TRX] Error finding transaction hash: {e}")
        return None
        
    except requests.exceptions.RequestException as e:
        log_error(logger, f"[TRX] TronGrid request failed: {e}")
        return None
    except Exception as e:
        log_error(logger, f"[TRX] Error finding transaction hash: {e}")
        return None


# ─── Main Router ──────────────────────────────────────────────────────────────

def get_transaction_hash(chain, address, expected_amount, token_contract=None):
    """
    Find the transaction hash that deposited the expected amount to the address.
    Used by fallback watcher when webhook didn't provide tx_hash.
    Routes to chain-specific implementations.
    """
    chain = chain.upper()
    
    try:
        if chain in ['BTC', 'LTC', 'DOGE']:
            if not BLOCKCYPHER_API_KEY:
                return None
            chain_map = {'BTC': 'btc', 'LTC': 'ltc', 'DOGE': 'doge'}
            coin = chain_map.get(chain)
            if not coin:
                return None
            url = f"https://api.blockcypher.com/v1/{coin}/main/addrs/{address}/full"
            params = {'token': BLOCKCYPHER_API_KEY, 'limit': 50}
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            expected_satoshis = int(round(expected_amount * 1e8))
            min_satoshis = int(expected_satoshis * 0.99)  # Allow 1% variance
            for tx in data.get('txs', []):
                for output in tx.get('outputs', []):
                    if address in output.get('addresses', []):
                        if output.get('value', 0) >= min_satoshis:
                            return tx.get('hash')
            return None
            
        elif chain == 'ETH':
            return _get_transaction_hash_eth_alchemy(address, expected_amount, token_contract)
            
        elif chain == 'BSC':
            return _get_transaction_hash_bsc_ankr(address, expected_amount, token_contract)
            
        elif chain == 'TRX':
            return _get_transaction_hash_trx_trongrid(address, expected_amount, token_contract)
            
        return None
    except Exception as e:
        log_error(logger, f"[{chain}] Error finding transaction hash: {e}")
        return None


# ─── Poll Confirmations (for fallback watcher) ───────────────────────────────

def poll_confirmations(chain, tx_hash, required_confirmations, address='', expected_amount=0, token_contract=None, timeout_seconds=300):
    """
    Poll for transaction confirmations until required reached or timeout.
    Returns final confirmation count or None on error/timeout.
    
    NOTE: address and expected_amount MUST be passed so that address/amount
    validation inside verify_transaction_rpc doesn't false-fail.
    """
    import time
    chain = chain.upper()
    start_time = time.time()
    
    while time.time() - start_time < timeout_seconds:
        result = verify_transaction_rpc(tx_hash, address, chain, expected_amount, token_contract)
        if result and result.get('valid'):
            confirmations = result.get('confirmations', 0)
            if confirmations >= required_confirmations:
                return confirmations
            log_info(logger, f"[{chain}] TX {tx_hash}: {confirmations}/{required_confirmations} confirmations, waiting...")
        time.sleep(10)  # Poll every 10 seconds
    
    log_warning(logger, f"[{chain}] Confirmation polling timeout for {tx_hash}")
    return None


if __name__ == '__main__':
    import sys
    if len(sys.argv) >= 4:
        tx_hash = sys.argv[1]
        address = sys.argv[2]
        chain = sys.argv[3]
        token = sys.argv[4] if len(sys.argv) > 4 else None
        result = verify_transaction_rpc(tx_hash, address, chain, 0, token)
        print(f"Verification result: {result}")
    else:
        print("Usage: python rpc_client.py <tx_hash> <address> <chain> [token_contract]")