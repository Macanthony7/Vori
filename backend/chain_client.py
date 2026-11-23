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


class ChainError(RuntimeError):
    """Provider unreachable or returned something unexpected. The watcher retries later."""


def observe(chain, asset, address, confirmations):
    contract = ASSETS[(chain, asset)]["contract"]
    if chain in ("BTC", "LTC", "DOGE"):
        return _utxo(chain, address, confirmations)
    if chain in RPC_URLS:
        return _evm(RPC_URLS[chain], address, contract, confirmations)
    if chain == "TRX":
        return _tron(address, contract)
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

def _tron(address, contract):
    headers = {"TRON-PRO-API-KEY": TRONGRID_API_KEY} if TRONGRID_API_KEY else {}
    if not contract:
        seen, confirmed = (
            int(_post(f"{TRONGRID_URL}/{api}/getaccount", {"address": address, "visible": True}, headers=headers)
                .get("balance", 0))
            for api in ("wallet", "walletsolidity")
        )
        return seen, confirmed
    hex20 = Base58Decoder.CheckDecode(address)[1:].hex()  # drop the 0x41 network byte
    payload = {"owner_address": address, "contract_address": contract, "function_selector": "balanceOf(address)",
               "parameter": hex20.rjust(64, "0"), "visible": True}
    values = []
    for api in ("wallet", "walletsolidity"):
        data = _post(f"{TRONGRID_URL}/{api}/triggerconstantcontract", payload, headers=headers)
        result = (data.get("constant_result") or [None])[0]
        if result is None:
            raise ChainError(f"TRC20 balanceOf failed: {str(data)[:200]}")
        values.append(int(result, 16) if result else 0)
    return values[0], values[1]
