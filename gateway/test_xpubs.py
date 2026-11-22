"""Self-check: run `python test_xpubs.py` from gateway/.

Fixtures: account-0 xpubs for the test phrase "abandon abandon ability", produced by the
offline tool; expected first addresses come from Ian Coleman's BIP39 test suite.
"""
from bip_utils import Base58Decoder, Base58Encoder

from xpubs import XpubError, address, normalize

CASES = {
    "BTC": ("xpub6CzDCPbtLrrn4VpVbyyQLHbdSMpZoHN4iuW64VswCyEpfjM2mJGdaHJ2DyuZwtst96E16VvcERb8BBeJdHSCVmAq9RhtRQg6eAZFrTKCNqf",
            "1Di3Vp7tBWtyQaDABLAjfWtF6V7hYKJtug"),
    "LTC": ("Ltub2YnugJJ5gy4i1y6vX5PBUx48gEyv3iQKxXEPmUoagbKdcKtEAznSGnDnwW1kBp5BMcF8CbneevwgDRz8F5FSjEX1tNrrWBN45Yoy8iMu2tY",
            "LQ4XU8RX2ULPmPq9FcUHdVmPVchP9nwXdn"),
    "DOGE": ("dgub8sjUaoeApiQWsiEpSWSGHBT7vSY2oFuD1ZzJwysKPDmd1MAkkj5taR4DJCh99fSfaVB6M8JzhPpRDQYJNCsnLEVmdPWDQH36vdqAP6ACqdo",
             "DPQH2AtuzkVSG6ovjKk4jbUmZ6iXLpgbJA"),
    "ETH": ("xpub6Cp1sx494AvnHH8Z4sCJWRoxUhSSJiLsnSzLiNJWqPAEfzaexCttU2YmKGMFog82mEqDGYkwPrEDsVVTyRD9aeHoipQDsw1bMHUUi1UH78B",
            "0xe5815d5902Ad612d49283DEdEc02100Bd44C2772"),
    "TRX": ("xpub6Bm41aYju7xDspcpezusAGEJ8MCV4ywXduYLqyDnbssPy4huN5s2GDZozZYuzH5wBht4YNfbmNtC7gPgPCDduzzFuCZmFfJKgZFB6FwEBJ2",
            "TA891Fu7vVz595BGQpNX2MCzr7yBcxuoC7"),
}


def rejects(chain, key, fragment):
    try:
        normalize(chain, key)
    except XpubError as e:
        assert fragment in str(e), f"{chain}: expected {fragment!r} in {e!r}"
        return
    raise AssertionError(f"{chain}: accepted bad key {key[:12]}")


def with_version(key, version_hex, depth=None):
    raw = bytearray(Base58Decoder.CheckDecode(key))
    raw[:4] = bytes.fromhex(version_hex)
    if depth is not None:
        raw[4] = depth
    return Base58Encoder.CheckEncode(bytes(raw))


def demo():
    for chain, (xpub, first) in CASES.items():
        got = address(chain, normalize(chain, xpub), 0)
        assert got == first, f"{chain}: {got} != {first}"
        assert address(chain, normalize(chain, xpub), 1) != first

    btc = CASES["BTC"][0]
    # LTC xpubs may arrive as "xpub" from other wallets: same key, same addresses.
    assert address("LTC", normalize("LTC", with_version(CASES["LTC"][0], "0488b21e")), 0) == CASES["LTC"][1]
    rejects("BTC", with_version(btc, "0488ade4"), "PRIVATE")         # xprv header
    rejects("BTC", with_version(btc, "04b24746"), "ypub/zpub")       # zpub header
    rejects("BTC", with_version(btc, "0488b21e", depth=4), "wrong level")
    rejects("BTC", btc[:-1] + ("A" if btc[-1] != "A" else "B"), "checksum")
    rejects("BTC", "", "not an extended")
    rejects("BTC", "x" * 500, "not an extended")
    rejects("SOL", btc, "unsupported chain")
    print("xpubs self-check OK")


if __name__ == "__main__":
    demo()
