import os
import json
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

def load_chains():
    with open(os.path.join(os.path.dirname(__file__), 'chains.json'), 'r') as f:
        return json.load(f)

def get_env(key, default=None):
    return os.getenv(key, default)

# =============================================================================
# QUICKNODE ENDPOINTS (Primary - single provider for all chains)
# =============================================================================
QUICKNODE_BTC_URL = get_env('QUICKNODE_BTC_URL', '')
QUICKNODE_ETH_URL = get_env('QUICKNODE_ETH_URL', '')
QUICKNODE_BSC_URL = get_env('QUICKNODE_BSC_URL', '')
QUICKNODE_LTC_URL = get_env('QUICKNODE_LTC_URL', '')
QUICKNODE_DOGE_URL = get_env('QUICKNODE_DOGE_URL', '')
QUICKNODE_TRX_URL = get_env('QUICKNODE_TRX_URL', '')

# =============================================================================
# ETH NETWORK TOGGLE (mainnet / sepolia testnet)
# Set ETH_NETWORK=sepolia in .env to run a testnet simulation.
# Defaults to 'mainnet' so production behavior is unchanged.
# =============================================================================
ETH_NETWORK = get_env('ETH_NETWORK', 'mainnet').strip().lower()
# Alchemy network slug used in the RPC URL: eth-mainnet or eth-sepolia
ETH_ALCHEMY_SLUG = 'eth-sepolia' if ETH_NETWORK == 'sepolia' else 'eth-mainnet'


def get_alchemy_eth_url():
    """Build the Alchemy RPC URL honoring ETH_NETWORK (mainnet/sepolia)."""
    return f"https://{ETH_ALCHEMY_SLUG}.g.alchemy.com/v2/{ALCHEMY_API_KEY}"


def is_eth_testnet():
    """True when ETH is pointed at Sepolia testnet (for simulation/testing)."""
    return ETH_NETWORK == 'sepolia'

def get_quicknode_url(chain):
    """Get QuickNode endpoint URL for a chain"""
    urls = {
        'BTC': QUICKNODE_BTC_URL,
        'ETH': QUICKNODE_ETH_URL,
        'BSC': QUICKNODE_BSC_URL,
        'LTC': QUICKNODE_LTC_URL,
        'DOGE': QUICKNODE_DOGE_URL,
        'TRX': QUICKNODE_TRX_URL,
    }
    return urls.get(chain.upper(), '')

# =============================================================================
# WEBHOOK SETTINGS (for Laravel integration)
# =============================================================================
WEBHOOK_URL = get_env('AEGIS_WEBHOOK_URL', 'https://aegiscapital.ltd/webhook/aegis')
WEBHOOK_TIMEOUT = int(get_env('AEGIS_WEBHOOK_TIMEOUT', '30'))

# =============================================================================
# LEGACY API KEYS (Deprecated - kept for backward compatibility during migration)
# =============================================================================
ALCHEMY_API_KEY = get_env('ALCHEMY_API_KEY', '')
BLOCKCYPHER_API_KEY = get_env('BLOCKCYPHER_API_KEY', '')
ANKR_API_KEY = get_env('ANKR_API_KEY', '')
TRONGRID_API_KEY = get_env('TRONGRID_API_KEY', '')

# =============================================================================
# MAIN WALLETS
# =============================================================================
MAIN_WALLET_BTC = get_env('MAIN_WALLET_BTC', '')
MAIN_WALLET_ETH = get_env('MAIN_WALLET_ETH', '')
MAIN_WALLET_BSC = get_env('MAIN_WALLET_BSC', '')
MAIN_WALLET_LTC = get_env('MAIN_WALLET_LTC', '')
MAIN_WALLET_DOGE = get_env('MAIN_WALLET_DOGE', '')
MAIN_WALLET_TRX = get_env('MAIN_WALLET_TRX', '')

# =============================================================================
# SEED PHRASE
# =============================================================================
SEED_PHRASE = get_env('AEGIS_SEED_PHRASE', '')

# =============================================================================
# APP KEY (for authentication)
# =============================================================================
APP_KEY = get_env('APP_KEY', '')

# =============================================================================
# TOKEN CONTRACT ADDRESSES (Hardcoded to prevent fake token spoofing)
# Only these contracts are accepted for token payments/sweeping
# =============================================================================
TOKEN_CONTRACTS = {
    # Ethereum Mainnet (ERC20)
    'USDT_ERC20': '0xdAC17F958D2ee523a2206206994597C13D831ec7',
    'USDC_ERC20': '0xA0b86a33E6441b8C4C8C8C8C8C8C8C8C8C8C8C8C8',
    
    # Binance Smart Chain (BEP20)
    'USDT_BEP20': '0x55d398326f99059fF775485246999027B3197955',
    'USDC_BEP20': '0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d',
    
    # TRON (TRC20)
    'USDT_TRC20': 'TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t',
    'USDC_TRC20': 'TEkxiTehnzSmSe2XqrBj4w32RUN966rdz8',
}

# Map token symbol + chain to contract address for easy lookup
TOKEN_CONTRACT_MAP = {
    ('USDT', 'ETH'): TOKEN_CONTRACTS['USDT_ERC20'],
    ('USDC', 'ETH'): TOKEN_CONTRACTS['USDC_ERC20'],
    ('USDT', 'BSC'): TOKEN_CONTRACTS['USDT_BEP20'],
    ('USDC', 'BSC'): TOKEN_CONTRACTS['USDC_BEP20'],
    ('USDT', 'TRX'): TOKEN_CONTRACTS['USDT_TRC20'],
    ('USDC', 'TRX'): TOKEN_CONTRACTS['USDC_TRC20'],
}

def get_token_contract(token_symbol, chain):
    """Get verified token contract address for a chain"""
    return TOKEN_CONTRACT_MAP.get((token_symbol.upper(), chain.upper()), '')

def is_valid_token_contract(token_contract, chain):
    """Check if a token contract is in our whitelist"""
    valid_contracts = [
        v for k, v in TOKEN_CONTRACT_MAP.items() 
        if k[1].upper() == chain.upper()
    ]
    return token_contract.lower() in [c.lower() for c in valid_contracts]

# =============================================================================
# CHAIN CONFIG
# =============================================================================
CHAINS = load_chains()

def get_chain_config(chain):
    return CHAINS.get(chain.upper(), {})

def get_main_wallet(chain):
    wallets = {
        'BTC': MAIN_WALLET_BTC,
        'ETH': MAIN_WALLET_ETH,
        'BSC': MAIN_WALLET_BSC,
        'LTC': MAIN_WALLET_LTC,
        'DOGE': MAIN_WALLET_DOGE,
        'TRX': MAIN_WALLET_TRX,
    }
    return wallets.get(chain.upper(), '')

def get_app_key():
    """Get Laravel APP_KEY for authentication"""
    return APP_KEY

def get_webhook_config():
    """Get webhook configuration for Laravel integration"""
    return {
        'url': WEBHOOK_URL,
        'timeout': WEBHOOK_TIMEOUT
    }