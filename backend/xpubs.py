"""xpub validation and receive-address derivation.

Public keys only: this module never sees or handles a private key.
See docs/security.md for why the server only ever stores account xpubs.
"""
from bip_utils import Base58Decoder, Base58Encoder, Bip44, Bip44Changes, Bip44Coins

# Chain id used in the API -> bip_utils coin.
# BSC uses the ETH xpub (coin type 60); its addresses are identical to ETH's.
CHAINS = {
    "BTC": Bip44Coins.BITCOIN,
    "LTC": Bip44Coins.LITECOIN,
    "DOGE": Bip44Coins.DOGECOIN,
    "ETH": Bip44Coins.ETHEREUM,
    "TRX": Bip44Coins.TRON,
}

# 4-byte BIP32 version prefixes we accept, and what bip_utils expects per chain.
XPUB, LTUB, DGUB = bytes.fromhex("0488b21e"), bytes.fromhex("019da462"), bytes.fromhex("02facafd")
PUBLIC_VERSIONS = {XPUB, LTUB, DGUB}
PRIVATE_VERSIONS = {bytes.fromhex(v) for v in ("0488ade4", "019d9cfe", "02fac398", "049d7878", "04b2430c")}
EXPECTED_VERSION = {"DOGE": DGUB}  # everything else: XPUB

MAX_KEY_LEN = 120  # base58 xpubs are 111 chars; anything far longer is junk


class XpubError(ValueError):
    pass


def normalize(chain, key):
    """Validate an account-level BIP44 xpub for `chain` and return it in bip_utils' format.

    Raises XpubError with a user-facing message on anything else.
    """
    if chain not in CHAINS:
        raise XpubError(f"unsupported chain {chain!r}")
    if not isinstance(key, str) or not key.strip() or len(key) > MAX_KEY_LEN:
        raise XpubError("not an extended public key")
    try:
        raw = Base58Decoder.CheckDecode(key.strip())
    except Exception:
        raise XpubError("not an extended public key (bad checksum or characters)")
    if len(raw) != 78:
        raise XpubError("not an extended public key (wrong length)")

    version, depth, child = raw[:4], raw[4], int.from_bytes(raw[9:13], "big")
    if version in PRIVATE_VERSIONS:
        raise XpubError("this is a PRIVATE key. Never share it. Paste the xpub (public key) instead")
    if version not in PUBLIC_VERSIONS:
        raise XpubError("unsupported key type: use a BIP44 xpub (not ypub/zpub)")
    if depth != 3 or child < 0x80000000:
        raise XpubError("wrong level: use the account xpub (path m/44'/coin'/account')")

    fixed = Base58Encoder.CheckEncode(EXPECTED_VERSION.get(chain, XPUB) + raw[4:])
    Bip44.FromExtendedKey(fixed, CHAINS[chain])  # final sanity check: valid curve point
    return fixed


def address(chain, xpub, index):
    """Receive address m/44'/coin'/account'/0/index from a normalized account xpub."""
    if not 0 <= index < 0x80000000:
        raise XpubError("address index out of range")
    acct = Bip44.FromExtendedKey(xpub, CHAINS[chain])
    return acct.Change(Bip44Changes.CHAIN_EXT).AddressIndex(index).PublicKey().ToAddress()
