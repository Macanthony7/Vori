"""Independent decoders for transactions the wallet frame signed: written from the formats' specs, not
from the frame's code, so a bug in the frame can't hide in both places.

EVM (EIP-1559 type 2 and legacy EIP-155) and Tron recover the signer from the signature itself.
Bitcoin-style (legacy P2PKH) checks each input's public key against the expected address.
"""
import hashlib

from bip_utils import Base58Encoder
from coincurve import PublicKey
from Crypto.Hash import RIPEMD160, keccak


def k256(b):
    h = keccak.new(digest_bits=256)
    h.update(b)
    return h.digest()


def num(b):
    return int.from_bytes(b, "big")


# --- RLP ------------------------------------------------------------------------------------------
def rlp_decode(b):
    def item(i):
        p = b[i]
        if p < 0x80:
            return b[i:i + 1], i + 1
        if p < 0xb8:
            n = p - 0x80
            return b[i + 1:i + 1 + n], i + 1 + n
        if p < 0xc0:
            ln = p - 0xb7
            n = num(b[i + 1:i + 1 + ln])
            return b[i + 1 + ln:i + 1 + ln + n], i + 1 + ln + n
        if p < 0xf8:
            n, start = p - 0xc0, i + 1
        else:
            ln = p - 0xf7
            n, start = num(b[i + 1:i + 1 + ln]), i + 1 + ln
        out, j = [], start
        while j < start + n:
            x, j = item(j)
            out.append(x)
        return out, start + n
    return item(0)[0]


def rlp_encode(x):
    def length(n, short, long_):
        if n < 56:
            return bytes([short + n])
        nb = n.to_bytes(8, "big").lstrip(b"\0")
        return bytes([long_ + len(nb)]) + nb
    if isinstance(x, list):
        body = b"".join(rlp_encode(i) for i in x)
        return length(len(body), 0xc0, 0xf7) + body
    if len(x) == 1 and x[0] < 0x80:
        return x
    return length(len(x), 0x80, 0xb7) + x


def evm(tx_hex):
    """{"chain_id", "nonce", "gas", "from", "to", "value", "token_to", "token_amount"}"""
    raw = bytes.fromhex(tx_hex.removeprefix("0x"))
    if raw[0] == 2:
        f = rlp_decode(raw[1:])
        chain_id, nonce, _tip, _max, gas, to, value, data, _acl, v, r, s = f
        digest, recid = k256(b"\x02" + rlp_encode(f[:9])), num(v)
    else:
        f = rlp_decode(raw)
        nonce, _gp, gas, to, value, data, v, r, s = f
        chain_id = (num(v) - 35) // 2
        digest = k256(rlp_encode(f[:6] + [chain_id.to_bytes(8, "big").lstrip(b"\0"), b"", b""]))
        recid = num(v) - 35 - 2 * chain_id
        chain_id = chain_id.to_bytes(8, "big")
    sig = r.rjust(32, b"\0") + s.rjust(32, b"\0") + bytes([recid])
    pub = PublicKey.from_signature_and_message(sig, digest, hasher=None)
    out = {"chain_id": num(chain_id), "nonce": num(nonce), "gas": num(gas),
           "from": "0x" + k256(pub.format(False)[1:])[-20:].hex(), "to": "0x" + to.hex(), "value": num(value)}
    if data:
        assert data[:4] == bytes.fromhex("a9059cbb"), "only ERC-20 transfer() is expected"
        out.update(token_to="0x" + data[16:36].hex(), token_amount=num(data[36:68]))
    return out


# --- Tron (protobuf) --------------------------------------------------------------------------------
def tron_address(b21):
    return Base58Encoder.CheckEncode(b21)


def _varint(b, i):
    n = shift = 0
    while True:
        c = b[i]
        n |= (c & 127) << shift
        i += 1
        shift += 7
        if c < 128:
            return n, i


def _fields(b):
    i, out = 0, []
    while i < len(b):
        key, i = _varint(b, i)
        if key & 7 == 0:
            v, i = _varint(b, i)
        elif key & 7 == 2:
            n, i = _varint(b, i)
            v, i = b[i:i + n], i + n
        else:
            raise ValueError(f"wire type {key & 7}")
        out.append((key >> 3, v))
    return out


def tron(tx_hex):
    """{"signer", "kind", "owner", "to", "amount", "contract", "fee_limit"}"""
    top = dict(_fields(bytes.fromhex(tx_hex)))
    raw, sig = top[1], top[2]
    txid = hashlib.sha256(raw).digest()
    pub = PublicKey.from_signature_and_message(sig[:64] + bytes([sig[64] - 27 if sig[64] >= 27 else sig[64]]),
                                               txid, hasher=None)
    rd = dict(_fields(raw))
    contract = dict(_fields(rd[11]))
    param = dict(_fields(contract[2]))
    val = dict(_fields(param[2]))
    out = {"signer": tron_address(b"\x41" + k256(pub.format(False)[1:])[-20:]),
           "kind": param[1].decode().split(".")[-1], "owner": tron_address(val[1]), "fee_limit": rd.get(18)}
    if out["kind"] == "TransferContract":
        out.update(to=tron_address(val[2]), amount=val[3])
    else:
        data = val[4]
        out.update(contract=tron_address(val[2]), to=tron_address(b"\x41" + data[16:36]), amount=num(data[36:68]))
    return out


# --- Bitcoin-style legacy P2PKH ------------------------------------------------------------------------
P2PKH_VERSION = {"BTC": b"\x00", "LTC": b"\x30", "DOGE": b"\x1e"}


def hash160(b):
    return RIPEMD160.new(hashlib.sha256(b).digest()).digest()


def p2pkh(chain, h160):
    return Base58Encoder.CheckEncode(P2PKH_VERSION[chain] + h160)


def utxo(chain, tx_hex):
    """{"inputs": [{"prev", "vout", "address"}], "outputs": [(address, value)]}"""
    b = bytes.fromhex(tx_hex)
    i = 4

    def compact():
        nonlocal i
        n = b[i]
        i += 1
        if n < 0xfd:
            return n
        size = {0xfd: 2, 0xfe: 4, 0xff: 8}[n]
        v = int.from_bytes(b[i:i + size], "little")
        i += size
        return v

    inputs = []
    for _ in range(compact()):
        prev, vout = b[i:i + 32][::-1].hex(), int.from_bytes(b[i + 32:i + 36], "little")
        i += 36
        size = compact()  # read the length first: it moves i
        script = b[i:i + size]
        i += len(script)
        i += 4  # sequence
        sig_len = script[0]
        pubkey = script[1 + sig_len + 1:]
        inputs.append({"prev": prev, "vout": vout, "address": p2pkh(chain, hash160(pubkey))})
    outputs = []
    for _ in range(compact()):
        value = int.from_bytes(b[i:i + 8], "little")
        i += 8
        size = compact()  # read the length first: it moves i
        script = b[i:i + size]
        i += len(script)
        assert script[:3] == b"\x76\xa9\x14" and script[-2:] == b"\x88\xac", "P2PKH outputs only"
        outputs.append((p2pkh(chain, script[3:23]), value))
    return {"inputs": inputs, "outputs": outputs}
