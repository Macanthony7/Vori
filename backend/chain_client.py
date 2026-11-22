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
TRONGRID_URL = os.getenv("TRONGRID_URL", "https://api.trongrid.io").rstrip("/")
TRONGRID_API_KEY = os.getenv("TRONGRID_API_KEY", "")


class ChainError(RuntimeError):
    """Provider unreachable or returned something unexpected. The watcher retries later."""


def observe(chain, asset, address, confirmations):
    contract = ASSETS[(chain, asset)]["contract"]
    if chain in ("BTC", "LTC", "DOGE"):
        return _utxo(chain, address, confirmations)
    if chain in ("ETH", "BSC"):
        return _evm(ETH_RPC_URL if chain == "ETH" else BSC_RPC_URL, address, contract, confirmations)
    if chain == "TRX":
        return _tron(address, contract)
    raise ChainError(f"unsupported chain {chain}")


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
        raise ChainError("RPC URL not configured (ETH_RPC_URL / BSC_RPC_URL)")
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
