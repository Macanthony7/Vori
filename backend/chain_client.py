"""Read-only blockchain queries: how much has an address received?

observe(chain, asset, address, confirmations) -> (seen, confirmed), both in smallest units:
  seen       everything sent to the address so far, including unconfirmed
  confirmed  the part with at least `confirmations` confirmations

Payment addresses are fresh (or reused only after receiving nothing), so "balance" and
"received" are the same thing for EVM and Tron. Configuration: docs/setup.md#providers
"""
import os

import requests
from bip_utils import Base58Decoder

from assets import ASSETS

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
except ImportError:
    pass

TIMEOUT = 15
BLOCKCYPHER_TOKEN = os.getenv("BLOCKCYPHER_TOKEN", "")
ETH_RPC_URL = os.getenv("ETH_RPC_URL", "")
BSC_RPC_URL = os.getenv("BSC_RPC_URL", "")
RPC_URLS = {"ETH": ETH_RPC_URL, "BSC": BSC_RPC_URL, "SEPOLIA": os.getenv("SEPOLIA_RPC_URL", "")}
TRONGRID_URL = os.getenv("TRONGRID_URL", "https://api.trongrid.io").rstrip("/")
TRONGRID_API_KEY = os.getenv("TRONGRID_API_KEY", "")
TRON_URLS = {"TRX": TRONGRID_URL, "NILE": os.getenv("NILE_TRONGRID_URL", "https://nile.trongrid.io").rstrip("/")}


class ChainError(RuntimeError):
    """Provider unreachable or returned something unexpected. The watcher retries later."""


def observe(chain, asset, address, confirmations):
    contract = ASSETS[(chain, asset)]["contract"]
    if chain in ("BTC", "LTC", "DOGE"):
        return _utxo(chain, address, confirmations)
    if chain in RPC_URLS:
        return _evm(RPC_URLS[chain], address, contract, confirmations)
    if chain in TRON_URLS:
        return _tron(TRON_URLS[chain], address, contract)
    raise ChainError(f"unsupported chain {chain}")


def balance(chain, asset, address):
    """Current balance in smallest units, including unconfirmed (what a wallet shows).

    Unlike observe(), it drops when funds are spent. EVM and Tron observe() already reads balances."""
    if chain in ("BTC", "LTC", "DOGE"):
        params = {"token": BLOCKCYPHER_TOKEN} if BLOCKCYPHER_TOKEN else {}
        data = _get(f"https://api.blockcypher.com/v1/{chain.lower()}/main/addrs/{address}/balance", params=params)
        try:
            return int(data["final_balance"])
        except (KeyError, TypeError, ValueError):
            raise ChainError("BlockCypher balance: unexpected response") from None
    return observe(chain, asset, address, 1)[0]


# --- Sending BTC / LTC / DOGE (the wallet frame signs; these only read and relay) ------------------

def _blockcypher(chain, path):
    return f"https://api.blockcypher.com/v1/{chain.lower()}/main{path}"


def _token():
    return {"token": BLOCKCYPHER_TOKEN} if BLOCKCYPHER_TOKEN else {}


def unspent(chain, address, min_confirmations=1):
    """Spendable outputs of an address: [{tx_hash, vout, value, confirmations}] (value in smallest units)."""
    data = _get(_blockcypher(chain, f"/addrs/{address}"), params={"unspentOnly": "true", "limit": 200, **_token()})
    try:
        return [{"tx_hash": t["tx_hash"], "vout": int(t["tx_output_n"]), "value": int(t["value"]),
                 "confirmations": int(t.get("confirmations", 0))}
                for t in data.get("txrefs") or []
                if t.get("tx_input_n") == -1 and not t.get("spent") and t.get("confirmations", 0) >= min_confirmations]
    except (KeyError, TypeError, ValueError):
        raise ChainError("BlockCypher unspent: unexpected response") from None


def fee_rates(chain):
    """Fee per byte (smallest units) for slow / normal / fast, from BlockCypher's per-kB estimates."""
    data = _get(_blockcypher(chain, ""), params=_token())
    try:
        per_byte = lambda key: max(1, -(-int(data[key]) // 1000))  # ceil(per kB / 1000)
        return {"slow": per_byte("low_fee_per_kb"), "normal": per_byte("medium_fee_per_kb"), "fast": per_byte("high_fee_per_kb")}
    except (KeyError, TypeError, ValueError):
        raise ChainError("BlockCypher fees: unexpected response") from None


def broadcast(chain, tx_hex):
    """Relay a signed transaction. Returns its hash. ChainError carries the node's reason on rejection."""
    try:
        r = requests.post(_blockcypher(chain, "/txs/push"), params=_token(), json={"tx": tx_hex}, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise ChainError(f"broadcast: {e}") from None
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code >= 300 or "tx" not in data:
        raise ChainError(f"rejected: {str(data.get('error') or r.text)[:300]}")
    return data["tx"]["hash"]


def _get(url, **kwargs):
    try:
        r = requests.get(url, timeout=TIMEOUT, **kwargs)
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError) as e:
        raise ChainError(f"GET {url.split('?')[0]}: {e}") from None


def _post(url, payload, **kwargs):
    try:
        r = requests.post(url, json=payload, timeout=TIMEOUT, **kwargs)
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError) as e:
        raise ChainError(f"POST {_redact(url)}: {e}") from None


def _redact(url):
    # RPC URLs often embed the API key in the path; never put it in logs.
    return url.split("//")[-1].split("/")[0]


# --- BTC / LTC / DOGE via BlockCypher ---------------------------------------------------------------

def _utxo(chain, address, confirmations):
    params = {"limit": 200}
    if BLOCKCYPHER_TOKEN:
        params["token"] = BLOCKCYPHER_TOKEN
    data = _get(f"https://api.blockcypher.com/v1/{chain.lower()}/main/addrs/{address}", params=params)
    # tx_input_n == -1 marks an output paid TO this address (a receive, not a spend).
    received = lambda refs: [t for t in refs or [] if t.get("tx_input_n") == -1]
    confirmed_refs = received(data.get("txrefs"))
    seen = sum(t["value"] for t in confirmed_refs + received(data.get("unconfirmed_txrefs")))
    confirmed = sum(t["value"] for t in confirmed_refs if t.get("confirmations", 0) >= confirmations)
    return seen, confirmed


# --- ETH / BSC via any JSON-RPC endpoint --------------------------------------------------------------

def _rpc(url, method, params):
    if not url:
        raise ChainError("RPC URL not configured (ETH_RPC_URL / BSC_RPC_URL / SEPOLIA_RPC_URL)")
    data = _post(url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    if "error" in data or "result" not in data:
        raise ChainError(f"{method}: {str(data.get('error'))[:200]}")
    return data["result"]


def _evm(url, address, contract, confirmations):
    latest = int(_rpc(url, "eth_blockNumber", []), 16)
    # A transaction in block B has (latest - B + 1) confirmations, so the balance as of block
    # latest - N + 1 contains exactly the funds with at least N confirmations.
    tags = (hex(latest), hex(latest - confirmations + 1))
    if contract:
        call = {"to": contract, "data": "0x70a08231" + address[2:].lower().rjust(64, "0")}  # balanceOf(address)
        values = [_rpc(url, "eth_call", [call, tag]) for tag in tags]
    else:
        values = [_rpc(url, "eth_getBalance", [address, tag]) for tag in tags]
    seen, confirmed = (int(v, 16) if v not in ("0x", None) else 0 for v in values)
    return seen, confirmed


# --- Sending native ETH / BNB (the wallet frame signs; these only read and relay) -----------------
GWEI = 10**9
MIN_TIP = GWEI // 10  # 0.1 gwei: quiet chains can report 0 tips, which may never get mined


def evm_account(chain, address):
    """(balance in wei at the latest block, next nonce counting pending transactions)."""
    url = RPC_URLS[chain]
    balance = int(_rpc(url, "eth_getBalance", [address, "latest"]), 16)
    return balance, int(_rpc(url, "eth_getTransactionCount", [address, "pending"]), 16)


def evm_fees(chain):
    """Fee options per speed, in wei. BSC: legacy gas price. Ethereum and testnets: EIP-1559 with tips
    from the 10th/50th/90th percentile of the last 10 blocks; max fee = 2 x next base fee + tip, which
    survives several full blocks of base-fee increases."""
    url = RPC_URLS[chain]
    if chain == "BSC":
        gp = int(_rpc(url, "eth_gasPrice", []), 16)
        return {"type": "legacy", "slow": {"gas_price": str(gp)}, "normal": {"gas_price": str(gp)},
                "fast": {"gas_price": str(gp * 12 // 10)}}
    hist = _rpc(url, "eth_feeHistory", [hex(10), "latest", [10, 50, 90]])
    try:
        base = int(hist["baseFeePerGas"][-1], 16)  # the next block's base fee
        rewards = [[int(r, 16) for r in block] for block in hist.get("reward") or []]
    except (KeyError, TypeError, ValueError):
        raise ChainError("eth_feeHistory: unexpected response") from None
    out = {"type": "eip1559"}
    for i, speed in enumerate(("slow", "normal", "fast")):
        tips = sorted(block[i] for block in rewards) or [0]
        tip = max(MIN_TIP, tips[len(tips) // 2])
        out[speed] = {"max_priority_fee_per_gas": str(tip), "max_fee_per_gas": str(2 * base + tip)}
    return out


def evm_broadcast(chain, tx_hex):
    """Relay a signed transaction; returns its hash. ChainError carries the node's reason."""
    return _rpc(RPC_URLS[chain], "eth_sendRawTransaction", ["0x" + tx_hex.removeprefix("0x")])


# --- TRX / TRC20 via TronGrid -----------------------------------------------------------------------
# "wallet/" answers from the latest block, "walletsolidity/" from the last solidified
# (irreversible) block, roughly 19 blocks behind: that is our confirmed view.

def _tron_headers(url):
    # The key belongs to mainnet TronGrid; never send it to a testnet host.
    return {"TRON-PRO-API-KEY": TRONGRID_API_KEY} if TRONGRID_API_KEY and url == TRONGRID_URL else {}


def _tron(url, address, contract):
    headers = _tron_headers(url)
    if not contract:
        seen, confirmed = (
            int(_post(f"{url}/{api}/getaccount", {"address": address, "visible": True}, headers=headers)
                .get("balance", 0))
            for api in ("wallet", "walletsolidity")
        )
        return seen, confirmed
    hex20 = Base58Decoder.CheckDecode(address)[1:].hex()  # drop the 0x41 network byte
    payload = {"owner_address": address, "contract_address": contract, "function_selector": "balanceOf(address)",
               "parameter": hex20.rjust(64, "0"), "visible": True}
    values = []
    for api in ("wallet", "walletsolidity"):
        data = _post(f"{url}/{api}/triggerconstantcontract", payload, headers=headers)
        result = (data.get("constant_result") or [None])[0]
        if result is None:
            raise ChainError(f"TRC20 balanceOf failed: {str(data)[:200]}")
        values.append(int(result, 16) if result else 0)
    return values[0], values[1]


# --- Sending TRX / TRC20 and tokens (the wallet frame signs; these only read and relay) ------------

def _tron_call(chain, path, payload=None):
    url = TRON_URLS[chain]
    return _post(f"{url}/wallet/{path}", payload or {}, headers=_tron_headers(url))


def tron_block(chain):
    """Latest block header, which a Tron transaction must reference (valid for ~18 hours)."""
    h = _tron_call(chain, "getnowblock").get("block_header", {}).get("raw_data", {})
    try:
        return {"number": int(h["number"]), "timestamp": int(h["timestamp"]), "tx_trie_root": h["txTrieRoot"],
                "parent_hash": h["parentHash"], "witness_address": h["witness_address"], "version": int(h.get("version", 0))}
    except (KeyError, TypeError, ValueError):
        raise ChainError("getnowblock: unexpected response") from None


def tron_params(chain):
    """Fees in sun: per bandwidth byte, per energy unit, and for activating a new account."""
    params = {p["key"]: p.get("value", 0) for p in _tron_call(chain, "getchainparameters").get("chainParameter", [])}
    try:
        return {"bandwidth_fee": int(params["getTransactionFee"]), "energy_fee": int(params["getEnergyFee"]),
                # 1 TRX system fee + 0.1 TRX burned for the bandwidth of an account-creating transfer
                "create_fee": int(params["getCreateNewAccountFeeInSystemContract"]) + int(params["getCreateAccountFee"])}
    except (KeyError, TypeError, ValueError):
        raise ChainError("getchainparameters: unexpected response") from None


def tron_account(chain, address):
    """{balance (sun), active, bandwidth, energy}: an address that never received TRX isn't active yet;
    bandwidth/energy are what it can use today without burning TRX."""
    acct = _tron_call(chain, "getaccount", {"address": address, "visible": True})
    res = _tron_call(chain, "getaccountresource", {"address": address, "visible": True})
    get = lambda k: int(res.get(k, 0))
    return {"balance": int(acct.get("balance", 0)), "active": bool(acct.get("address")),
            "bandwidth": max(0, get("freeNetLimit") - get("freeNetUsed")) + max(0, get("NetLimit") - get("NetUsed")),
            "energy": max(0, get("EnergyLimit") - get("EnergyUsed"))}


def tron_token_energy(chain, contract, owner, to, amount):
    """Energy a TRC20 transfer(to, amount) from `owner` would use (simulated, nothing is sent)."""
    to_hex = Base58Decoder.CheckDecode(to)[1:].hex()
    data = _tron_call(chain, "triggerconstantcontract", {
        "owner_address": owner, "contract_address": contract, "function_selector": "transfer(address,uint256)",
        "parameter": to_hex.rjust(64, "0") + format(amount, "064x"), "visible": True})
    if not (data.get("result") or {}).get("result") or "energy_used" not in data:
        raise ChainError(f"TRC20 transfer simulation failed: {str(data.get('result'))[:200]}")
    return int(data["energy_used"])


def tron_broadcast(chain, tx_hex):
    """Relay a signed Transaction (protobuf, hex). Returns its id."""
    data = _tron_call(chain, "broadcasthex", {"transaction": tx_hex})
    if not data.get("result"):
        msg = data.get("message", "")
        try:
            msg = bytes.fromhex(msg).decode()  # TronGrid hex-encodes some messages
        except ValueError:
            pass
        raise ChainError(f"rejected: {data.get('code', '')} {msg}"[:300])
    return data["txid"]


def token_balance(chain, contract, address):
    """ERC-20 balanceOf at the latest block."""
    call = {"to": contract, "data": "0x70a08231" + address[2:].lower().rjust(64, "0")}
    v = _rpc(RPC_URLS[chain], "eth_call", [call, "latest"])
    return int(v, 16) if v not in ("0x", None) else 0


def evm_token_gas(chain, contract, sender, to, amount):
    """Gas an ERC-20 transfer(to, amount) from `sender` would use (eth_estimateGas, nothing is sent)."""
    data = "0xa9059cbb" + to[2:].lower().rjust(64, "0") + format(amount, "064x")
    return int(_rpc(RPC_URLS[chain], "eth_estimateGas", [{"from": sender, "to": contract, "data": data}]), 16)


def tx_status(chain, tx_hash):
    """None while pending, True once included and successful, False if included but failed."""
    if chain in TRON_URLS:
        info = _tron_call(chain, "gettransactioninfobyid", {"value": tx_hash})
        if not info.get("blockNumber"):
            return None
        return info.get("result") != "FAILED"
    receipt = _rpc(RPC_URLS[chain], "eth_getTransactionReceipt", [tx_hash])
    if not receipt or not receipt.get("blockNumber"):
        return None
    return receipt.get("status") == "0x1"
