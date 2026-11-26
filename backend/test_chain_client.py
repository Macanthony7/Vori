"""chain_client against recorded provider responses (BlockCypher, JSON-RPC, TronGrid), no network.

The shapes follow the providers' real answers (checked live on Sepolia and Nile). This covers the
parsing every other test fakes away: amounts, confirmations, fee math, error handling.
Run: python -m pytest backend/test_chain_client.py   (or python run_tests.py)
"""
import os
import tempfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault("GATEWAY_DB", os.path.join(tempfile.mkdtemp(), "cc.db"))
os.environ.setdefault("GATEWAY_DB_KEY", os.urandom(32).hex())

import pytest  # noqa: E402
import requests  # noqa: E402

import chain_client as cc  # noqa: E402

ADDR = "0x" + "ab" * 20
TRON = "TUEZSdKsoDHQMeZwihtdoBiN46zxhGWYdH"
USDT_TRON = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"


class Reply:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status
        self.headers = {"content-type": "application/json"}
        self.text = str(data)

    def json(self):
        if isinstance(self.data, Exception):
            raise self.data
        return self.data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


@pytest.fixture
def net(monkeypatch):
    """Route requests by URL (and JSON-RPC method) to canned replies. net.calls records every call."""
    routes, calls = {}, []

    def handle(method, url, json=None, **kw):
        calls.append((method, url, json, kw))
        key = json.get("method") if isinstance(json, dict) and "jsonrpc" in json else None
        # Longest URL part first: "/getaccountresource" must not be caught by "/getaccount".
        for (m, part, rpc), reply in sorted(routes.items(), key=lambda r: -len(r[0][1])):
            if m == method and part in url and rpc in (None, key):
                out = reply(url, json, kw) if callable(reply) else reply
                if isinstance(out, Exception):
                    raise out
                return out if isinstance(out, Reply) else Reply(out)
        raise AssertionError(f"unexpected {method} {url} {key}")

    monkeypatch.setattr(cc.requests, "get", lambda url, **kw: handle("GET", url, **kw))
    monkeypatch.setattr(cc.requests, "post", lambda url, json=None, **kw: handle("POST", url, json, **kw))
    monkeypatch.setattr(cc, "RPC_URLS", {"ETH": "https://eth.test/KEY", "BSC": "https://bsc.test", "SEPOLIA": ""})

    class Net:
        def on(self, method, part, reply, rpc=None):
            routes[(method, part, rpc)] = reply
    n = Net()
    n.calls = calls
    return n


def rpc(result):
    return {"jsonrpc": "2.0", "id": 1, "result": result}


# --- BTC / LTC / DOGE (BlockCypher) ---------------------------------------------------------------
def test_utxo_observe_counts_receives_and_confirmations(net):
    net.on("GET", "/btc/main/addrs/1abc", {
        "txrefs": [{"tx_hash": "a1", "tx_input_n": -1, "value": 30_000, "confirmations": 5},
                   {"tx_hash": "a2", "tx_input_n": -1, "value": 20_000, "confirmations": 1},
                   {"tx_hash": "s1", "tx_input_n": 0, "value": 99_999, "confirmations": 9}],  # a spend: ignored
        "unconfirmed_txrefs": [{"tx_hash": "u1", "tx_input_n": -1, "value": 7_000}]})
    assert cc.observe("BTC", "BTC", "1abc", 2) == (57_000, 30_000)


def test_utxo_tx_hashes_oldest_first_without_spends(net):
    net.on("GET", "/ltc/main/addrs/Labc", {
        "txrefs": [{"tx_hash": "new", "tx_input_n": -1}, {"tx_hash": "old", "tx_input_n": -1},
                   {"tx_hash": "spend", "tx_input_n": 0}],
        "unconfirmed_txrefs": [{"tx_hash": "pending", "tx_input_n": -1}]})
    assert cc.tx_hashes("LTC", "LTC", "Labc", datetime.now(timezone.utc)) == ["pending", "old", "new"]


def test_utxo_balance_unspent_fees_and_broadcast(net, monkeypatch):
    monkeypatch.setattr(cc, "BLOCKCYPHER_TOKEN", "tok")
    net.on("GET", "/doge/main/addrs/Dabc/balance", {"final_balance": 123})
    assert cc.balance("DOGE", "DOGE", "Dabc") == 123
    assert net.calls[-1][3]["params"] == {"token": "tok"}, "token sent when configured"
    net.on("GET", "/btc/main/addrs/1u", {"txrefs": [
        {"tx_hash": "h1", "tx_output_n": 1, "value": 5000, "confirmations": 3, "tx_input_n": -1},
        {"tx_hash": "h2", "tx_output_n": 0, "value": 9000, "confirmations": 0, "tx_input_n": -1},
        {"tx_hash": "h3", "tx_output_n": 2, "value": 7000, "confirmations": 6, "tx_input_n": -1, "spent": True}]})
    assert cc.unspent("BTC", "1u") == [{"tx_hash": "h1", "vout": 1, "value": 5000, "confirmations": 3}]
    net.on("GET", "/btc/main", {"low_fee_per_kb": 1500, "medium_fee_per_kb": 10_001, "high_fee_per_kb": 20_000})
    assert cc.fee_rates("BTC") == {"slow": 2, "normal": 11, "fast": 20}, "per byte, rounded up"
    net.on("POST", "/txs/push", Reply({"tx": {"hash": "f00d"}}, 201))
    assert cc.broadcast("BTC", "0100") == "f00d"
    net.on("POST", "/txs/push", Reply({"error": "Error validating transaction: insufficient priority"}, 400))
    with pytest.raises(cc.ChainError, match="insufficient priority"):
        cc.broadcast("BTC", "0100")


def test_utxo_bad_responses_are_chain_errors(net):
    net.on("GET", "/btc/main/addrs/1x/balance", {"nope": 1})
    with pytest.raises(cc.ChainError):
        cc.balance("BTC", "BTC", "1x")
    net.on("GET", "/btc/main/addrs/1y", Reply({}, 429))
    with pytest.raises(cc.ChainError, match="429"):
        cc.observe("BTC", "BTC", "1y", 1)
    net.on("GET", "/btc/main/addrs/1z", ValueError("not json"))
    with pytest.raises(cc.ChainError):
        cc.observe("BTC", "BTC", "1z", 1)


# --- ETH / BSC (JSON-RPC) ---------------------------------------------------------------------------
def test_evm_observe_reads_latest_and_confirmed_block(net):
    tags = []
    net.on("POST", "eth.test", rpc(hex(1000)), rpc="eth_blockNumber")
    net.on("POST", "eth.test", lambda u, j, kw: tags.append(j["params"][1]) or rpc(hex(5) if j["params"][1] == hex(1000) else "0x2"),
           rpc="eth_getBalance")
    assert cc.observe("ETH", "ETH", ADDR, 12) == (5, 2)
    assert tags == [hex(1000), hex(1000 - 12 + 1)], "confirmed = balance at latest - N + 1"
    net.on("POST", "eth.test", lambda u, j, kw: rpc("0x" + "0" * 63 + ("9" if j["params"][1] in (hex(1000), "latest") else "4")),
           rpc="eth_call")
    assert cc.observe("ETH", "USDT", ADDR, 12) == (9, 4)
    assert cc.token_balance("ETH", "0xdac17f958d2ee523a2206206994597c13d831ec7", ADDR) == 9


def test_evm_account_fees_gas_broadcast_status(net):
    net.on("POST", "eth.test", rpc("0xde0b6b3a7640000"), rpc="eth_getBalance")
    net.on("POST", "eth.test", rpc("0x7"), rpc="eth_getTransactionCount")
    assert cc.evm_account("ETH", ADDR) == (10**18, 7)
    net.on("POST", "eth.test", rpc({"baseFeePerGas": ["0x1", hex(10 * cc.GWEI)],
                                    "reward": [["0x0", hex(2 * cc.GWEI), hex(5 * cc.GWEI)]] * 3}), rpc="eth_feeHistory")
    fees = cc.evm_fees("ETH")
    assert fees["type"] == "eip1559"
    assert fees["slow"]["max_priority_fee_per_gas"] == str(cc.MIN_TIP), "zero tips are raised to the minimum"
    assert fees["normal"] == {"max_priority_fee_per_gas": str(2 * cc.GWEI), "max_fee_per_gas": str(22 * cc.GWEI)}
    net.on("POST", "bsc.test", rpc(hex(3 * cc.GWEI)), rpc="eth_gasPrice")
    assert cc.evm_fees("BSC")["fast"] == {"gas_price": str(36 * cc.GWEI // 10)}
    net.on("POST", "eth.test", rpc(hex(51_234)), rpc="eth_estimateGas")
    assert cc.evm_token_gas("ETH", "0xc", ADDR, ADDR, 10) == 51_234
    net.on("POST", "eth.test", rpc("0x" + "aa" * 32), rpc="eth_sendRawTransaction")
    assert cc.evm_broadcast("ETH", "02f8") == "0x" + "aa" * 32
    assert net.calls[-1][2]["params"] == ["0x02f8"]
    net.on("POST", "eth.test", rpc(None), rpc="eth_getTransactionReceipt")
    assert cc.tx_status("ETH", "0x1") is None
    net.on("POST", "eth.test", rpc({"blockNumber": "0x5", "status": "0x0"}), rpc="eth_getTransactionReceipt")
    assert cc.tx_status("ETH", "0x1") is False


def test_evm_errors_never_leak_the_rpc_key(net):
    net.on("POST", "eth.test", {"jsonrpc": "2.0", "id": 1, "error": {"message": "rate limited"}}, rpc="eth_blockNumber")
    with pytest.raises(cc.ChainError, match="rate limited"):
        cc.observe("ETH", "ETH", ADDR, 1)
    net.on("POST", "eth.test", requests.ConnectionError("boom"), rpc="eth_getBalance")
    with pytest.raises(cc.ChainError) as e:
        cc.evm_account("ETH", ADDR)
    assert "KEY" not in str(e.value), "the URL path (API key) is redacted"
    with pytest.raises(cc.ChainError, match="not configured"):
        cc.observe("SEPOLIA", "ETH", ADDR, 1) if ("SEPOLIA", "ETH") in cc.ASSETS else cc._rpc("", "x", [])


def test_evm_token_tx_hashes_search_logs_in_chunks(net):
    net.on("POST", "eth.test", rpc(hex(20_000)), rpc="eth_blockNumber")
    ranges = []

    def logs(u, j, kw):
        q = j["params"][0]
        ranges.append((int(q["fromBlock"], 16), int(q["toBlock"], 16)))
        assert q["topics"][2] == "0x" + "0" * 24 + ADDR[2:], "filtered on the recipient"
        return rpc([{"transactionHash": f"0x{q['toBlock'][2:]}"}])
    net.on("POST", "eth.test", logs, rpc="eth_getLogs")
    since = datetime.now(timezone.utc) - timedelta(seconds=12 * 700)  # ~700 blocks ago
    hashes = cc.tx_hashes("ETH", "USDT", ADDR, since)
    assert ranges[0] == (20_000 - cc.LOG_CHUNK + 1, 20_000) and len(ranges) == 2, ranges
    assert hashes == [f"0x{hex(ranges[1][1])[2:]}", f"0x{hex(20_000)[2:]}"], "oldest first"
    assert cc.tx_hashes("ETH", "ETH", ADDR, since) is None, "native ETH can't be searched"


# --- Tron (TronGrid) ----------------------------------------------------------------------------------
def test_tron_observe_native_and_token(net, monkeypatch):
    net.on("POST", "/wallet/getaccount", {"balance": 7_000_000})
    net.on("POST", "/walletsolidity/getaccount", {"balance": 5_000_000})
    assert cc.observe("TRX", "TRX", TRON, 20) == (7_000_000, 5_000_000)
    net.on("POST", "/wallet/triggerconstantcontract", {"constant_result": ["0" * 62 + "0a"]})
    net.on("POST", "/walletsolidity/triggerconstantcontract", {"constant_result": [""]})
    assert cc.observe("TRX", "USDT", TRON, 20) == (10, 0)
    net.on("POST", "/walletsolidity/triggerconstantcontract", {"result": {"message": "bad"}})
    with pytest.raises(cc.ChainError, match="balanceOf"):
        cc.observe("TRX", "USDT", TRON, 20)
    monkeypatch.setattr(cc, "TRONGRID_API_KEY", "secret")
    cc.observe("TRX", "TRX", TRON, 20)
    assert net.calls[-1][3]["headers"] == {"TRON-PRO-API-KEY": "secret"}


def test_tron_send_helpers(net):
    net.on("POST", "/wallet/getnowblock", {"block_header": {"raw_data": {
        "number": 9, "timestamp": 100, "txTrieRoot": "aa", "parentHash": "bb", "witness_address": "41cc", "version": 30}}})
    assert cc.tron_block("TRX") == {"number": 9, "timestamp": 100, "tx_trie_root": "aa", "parent_hash": "bb",
                                    "witness_address": "41cc", "version": 30}
    net.on("POST", "/wallet/getchainparameters", {"chainParameter": [
        {"key": "getTransactionFee", "value": 1000}, {"key": "getEnergyFee", "value": 100},
        {"key": "getCreateNewAccountFeeInSystemContract", "value": 1_000_000}, {"key": "getCreateAccountFee", "value": 100_000}]})
    assert cc.tron_params("TRX") == {"bandwidth_fee": 1000, "energy_fee": 100, "create_fee": 1_100_000}
    net.on("POST", "/wallet/getaccount", {"address": TRON, "balance": 3})
    net.on("POST", "/wallet/getaccountresource", {"freeNetLimit": 600, "freeNetUsed": 100, "EnergyLimit": 5, "EnergyUsed": 9})
    assert cc.tron_account("TRX", TRON) == {"balance": 3, "active": True, "bandwidth": 500, "energy": 0}
    net.on("POST", "/wallet/triggerconstantcontract", {"result": {"result": True}, "energy_used": 64_285})
    assert cc.tron_token_energy("TRX", USDT_TRON, TRON, TRON, 5) == 64_285
    net.on("POST", "/wallet/broadcasthex", {"result": True, "txid": "beef"})
    assert cc.tron_broadcast("TRX", "0a02") == "beef"
    net.on("POST", "/wallet/broadcasthex", {"result": False, "code": "SIGERROR", "message": "76616c6964617465"})
    with pytest.raises(cc.ChainError, match="SIGERROR validate"):
        cc.tron_broadcast("TRX", "0a02")
    net.on("POST", "/wallet/gettransactioninfobyid", {})
    assert cc.tx_status("TRX", "beef") is None
    net.on("POST", "/wallet/gettransactioninfobyid", {"blockNumber": 5, "result": "FAILED"})
    assert cc.tx_status("TRX", "beef") is False
    net.on("POST", "/wallet/getchainparameters", {"chainParameter": []})
    with pytest.raises(cc.ChainError):
        cc.tron_params("TRX")


def test_tron_tx_hashes(net):
    net.on("GET", f"/v1/accounts/{TRON}/transactions/trc20", {"success": True, "data": [
        {"transaction_id": "t2", "to": TRON, "token_info": {"address": USDT_TRON}},
        {"transaction_id": "fake", "to": TRON, "token_info": {"address": "TFakeToken"}},  # look-alike token
        {"transaction_id": "t1", "to": TRON, "token_info": {"address": USDT_TRON}}]})
    assert cc.tx_hashes("TRX", "USDT", TRON, datetime.now(timezone.utc)) == ["t1", "t2"]
    net.on("GET", f"/v1/accounts/{TRON}/transactions", {"success": True, "data": [
        {"txID": "n2", "ret": [{"contractRet": "SUCCESS"}], "raw_data": {"contract": [{"type": "TransferContract"}]}},
        {"txID": "bad", "ret": [{"contractRet": "REVERT"}], "raw_data": {"contract": [{"type": "TransferContract"}]}},
        {"txID": "n1", "ret": [{"contractRet": "SUCCESS"}], "raw_data": {"contract": [{"type": "TransferContract"}]}}]})
    assert cc.tx_hashes("TRX", "TRX", TRON, datetime.now(timezone.utc)) == ["n1", "n2"]
    net.on("GET", f"/v1/accounts/{TRON}/transactions", {"success": False, "error": "x"})
    with pytest.raises(cc.ChainError):
        cc.tx_hashes("TRX", "TRX", TRON, datetime.now(timezone.utc))
