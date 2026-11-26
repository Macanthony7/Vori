"""What can be paid with, on which chain, and how amounts are written.

Docs: docs/api.md#assets
"""
import os
from decimal import Decimal, InvalidOperation
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")  # some modules import assets before db

# (chain, asset) -> settings. `xpub` = which registered xpub the address comes from.
# Confirmations are the common industry defaults. TRX counts as confirmed once the block is
# "solidified" (irreversible, ~19 blocks), which is what the confirmed balance endpoints return.
ASSETS = {
    ("BTC", "BTC"):   dict(xpub="BTC",  decimals=8,  confirmations=2,  contract=None),
    ("LTC", "LTC"):   dict(xpub="LTC",  decimals=8,  confirmations=6,  contract=None),
    ("DOGE", "DOGE"): dict(xpub="DOGE", decimals=8,  confirmations=6,  contract=None),
    ("ETH", "ETH"):   dict(xpub="ETH",  decimals=18, confirmations=12, contract=None),
    ("ETH", "USDT"):  dict(xpub="ETH",  decimals=6,  confirmations=12, contract="0xdAC17F958D2ee523a2206206994597C13D831ec7"),
    ("BSC", "BNB"):   dict(xpub="ETH",  decimals=18, confirmations=15, contract=None),
    # BSC-USD (USDT on BSC) uses 18 decimals, unlike USDT on Ethereum and Tron.
    ("BSC", "USDT"):  dict(xpub="ETH",  decimals=18, confirmations=15, contract="0x55d398326f99059fF775485246999027B3197955"),
    ("TRX", "TRX"):   dict(xpub="TRX",  decimals=6,  confirmations=20, contract=None),
    ("TRX", "USDT"):  dict(xpub="TRX",  decimals=6,  confirmations=20, contract="TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"),
}

# Test networks, only with GATEWAY_TESTNETS=1 (never in production): test coins have no value.
TESTNETS = os.getenv("GATEWAY_TESTNETS") == "1"
if TESTNETS:
    ASSETS[("SEPOLIA", "ETH")] = dict(xpub="ETH", decimals=18, confirmations=3, contract=None)
    # Aave's Sepolia faucet USDT (app.aave.com/faucet, testnet mode). 6 decimals like real USDT.
    ASSETS[("SEPOLIA", "USDT")] = dict(xpub="ETH", decimals=6, confirmations=3, contract="0xaA8E23Fb1079EA71e0a56F48a2aA51851D8433D0")
    # Tron Nile testnet (nileex.io faucet gives TRX and this test USDT).
    ASSETS[("NILE", "TRX")] = dict(xpub="TRX", decimals=6, confirmations=20, contract=None)
    ASSETS[("NILE", "USDT")] = dict(xpub="TRX", decimals=6, confirmations=20, contract="TXYZopYRdj2D9XRtbG411XZZ3kM5VkAeBf")

# EVM chains: chain id used when signing (EIP-155 replay protection) and the native asset.
EVM_CHAINS = {"ETH": dict(chain_id=1, native="ETH"), "BSC": dict(chain_id=56, native="BNB"),
              "SEPOLIA": dict(chain_id=11155111, native="ETH")}
TRON_CHAINS = ("TRX", "NILE")  # native asset TRX on both
# Block explorers: (transaction URL prefix, address URL prefix).
EXPLORERS = {
    "BTC": ("https://blockchair.com/bitcoin/transaction/", "https://blockchair.com/bitcoin/address/"),
    "LTC": ("https://blockchair.com/litecoin/transaction/", "https://blockchair.com/litecoin/address/"),
    "DOGE": ("https://blockchair.com/dogecoin/transaction/", "https://blockchair.com/dogecoin/address/"),
    "ETH": ("https://etherscan.io/tx/", "https://etherscan.io/address/"),
    "BSC": ("https://bscscan.com/tx/", "https://bscscan.com/address/"),
    "SEPOLIA": ("https://sepolia.etherscan.io/tx/", "https://sepolia.etherscan.io/address/"),
    "TRX": ("https://tronscan.org/#/transaction/", "https://tronscan.org/#/address/"),
    "NILE": ("https://nile.tronscan.org/#/transaction/", "https://nile.tronscan.org/#/address/"),
}

MAX_UNITS = 10**15  # cap per payment, in whole coins/tokens


class AmountError(ValueError):
    pass


def lookup(chain, asset):
    key = (str(chain).upper(), str(asset).upper())
    if key not in ASSETS:
        raise AmountError(f"unsupported chain/asset {key[0]}/{key[1]}")
    return key, ASSETS[key]


def to_units(amount, decimals):
    """'12.50' -> 12500000 (for 6 decimals). Strings only, so floats never round silently."""
    if not isinstance(amount, str):
        raise AmountError("amount must be a decimal string, e.g. \"12.50\"")
    try:
        value = Decimal(amount.strip())
    except InvalidOperation:
        raise AmountError("amount must be a decimal string, e.g. \"12.50\"")
    if not value.is_finite() or value <= 0:
        raise AmountError("amount must be greater than 0")
    if value > MAX_UNITS:
        raise AmountError("amount is too large")
    if -value.as_tuple().exponent > decimals:
        raise AmountError(f"amount has more than {decimals} decimal places")
    return int(value.scaleb(decimals))


def from_units(units, decimals):
    """12500000 -> '12.5'"""
    return format(Decimal(int(units)).scaleb(-decimals).normalize(), "f")
