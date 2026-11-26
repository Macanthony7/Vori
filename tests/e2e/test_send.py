"""Send, end to end: the real offline frame plans and signs, the fake chain records what was relayed,
and decode.py checks every signed transaction independently (signer, amounts, destination, nonces)."""
import re

import httpx
from playwright.sync_api import expect

import decode
from conftest import frame, import_wallet, unlock

# Account-0 main addresses (index 0) of the test phrase.
BTC_MAIN = "1LqBGSKuX5yYUonjxT5qGfpUsXKYYWeabA"
ETH_MAIN = "0x9858effd232b4033e47d90003d41ec34ecaeda94"
TRX_MAIN = "TUEZSdKsoDHQMeZwihtdoBiN46zxhGWYdH"
TRX_USDT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
TO_BTC = "1BoatSLRHtKNngkdXEeobR76b53LETtpyT"
TO_ETH = "0x000000000000000000000000000000000000dead"
TO_TRX = "TXYZopYRdj2D9XRtbG411XZZ3kM5VkAeBf"


def send(page, network, to, amount):
    """Open Send on the balance row for `network`, review, confirm in the frame. Returns the frame."""
    row = page.locator("#balances li", has=page.locator(".network", has_text=re.compile(f"^{re.escape(network)}$")))
    row.get_by_role("button", name=re.compile("^Send")).click()
    page.fill("#send-to", to)
    page.fill("#send-amount", amount)
    page.click("#send-review")
    f = frame(page)
    expect(f.locator("#confirm")).to_be_visible()
    return f


def relayed_since(chain, n):
    return [tx for c, tx in chain.relayed[n:]]


def test_send_btc_with_change(page, chain):
    chain.utxos[("BTC", BTC_MAIN)] = [{"tx_hash": "11" * 32, "vout": 1, "value": 100_000, "confirmations": 3}]
    chain.balances[("BTC", "BTC", BTC_MAIN)] = 100_000
    import_wallet(page)
    unlock(page)
    n = len(chain.relayed)
    f = send(page, "Bitcoin", TO_BTC, "0.0005")
    assert "0.0005" in f.locator("#cf-amount").inner_text() and TO_BTC in f.locator("#cf-to").inner_text()
    f.locator("#cf-ok").click()
    expect(page.locator("#send-result")).to_contain_text("Sent")
    (tx,) = relayed_since(chain, n)
    d = decode.utxo("BTC", tx)
    assert d["inputs"] == [{"prev": "11" * 32, "vout": 1, "address": BTC_MAIN}], "spends our own coin"
    outputs = dict(d["outputs"])
    assert outputs[TO_BTC] == 50_000
    fee = 100_000 - sum(outputs.values())
    assert set(outputs) == {TO_BTC, BTC_MAIN} and 0 < fee < 5_000, ("change back to the main address", fee)


def test_send_eth(page, chain):
    chain.balances[("ETH", "ETH", "0x9858EfFD232B4033E47d90003D41EC34EcaEda94")] = 10**18
    chain.nonces[("ETH", "0x9858EfFD232B4033E47d90003D41EC34EcaEda94")] = 4
    import_wallet(page)
    unlock(page)
    n = len(chain.relayed)
    send(page, "Ethereum", TO_ETH, "0.1").locator("#cf-ok").click()
    expect(page.locator("#send-result")).to_contain_text("Sent")
    (tx,) = relayed_since(chain, n)
    d = decode.evm(tx)
    assert (d["chain_id"], d["from"], d["to"], d["value"], d["nonce"], d["gas"]) == \
        (1, ETH_MAIN, TO_ETH, 10**17, 4, 21_000), d


def test_send_trx_to_new_address(page, chain):
    chain.balances[("TRX", "TRX", TRX_MAIN)] = 10_000_000
    import_wallet(page)
    unlock(page)
    n = len(chain.relayed)
    f = send(page, "Tron", TO_TRX, "1.5")
    assert "TRX" in f.locator("#cf-fee").inner_text()
    f.locator("#cf-ok").click()
    expect(page.locator("#send-result")).to_contain_text("Sent")
    (tx,) = relayed_since(chain, n)
    d = decode.tron(tx)
    assert (d["kind"], d["signer"], d["owner"], d["to"], d["amount"]) == \
        ("TransferContract", TRX_MAIN, TRX_MAIN, TO_TRX, 1_500_000), d


def test_send_usdt_on_tron_with_gas_topup(page, server, chain, tick):
    import_wallet(page)
    unlock(page)
    # A paid USDT payment: its address holds the USDT but no TRX for fees.
    page.click("#charge summary")
    page.fill("#charge-amount", "3")
    page.select_option("#charge-network", "TRX/USDT")
    page.click("#charge-create")
    pid = page.locator("#charge-result a").get_attribute("href").split("/")[-1]
    address = httpx.get(f"{server}/pay/{pid}/status").json()["address"]
    chain.pay("TRX", "USDT", address, 3_000_000)
    tick()
    chain.balances[("TRX", "TRX", TRX_MAIN)] = 50_000_000  # gas comes from the main address
    page.reload()
    unlock(page)

    n = len(chain.relayed)
    send(page, "Tron (TRC-20)", TO_TRX, "3").locator("#cf-ok").click()
    expect(page.locator("#send-result")).to_contain_text("Sent", timeout=60_000)
    topup, transfer = (decode.tron(tx) for tx in relayed_since(chain, n))
    assert (topup["kind"], topup["signer"], topup["to"]) == ("TransferContract", TRX_MAIN, address), topup
    assert topup["amount"] > 1_100_000, "top-up covers activating the new address"
    assert (transfer["kind"], transfer["signer"], transfer["contract"], transfer["to"], transfer["amount"]) == \
        ("TriggerSmartContract", address, TRX_USDT, TO_TRX, 3_000_000), transfer
    assert 0 < transfer["fee_limit"] <= 100_000_000


def test_frame_refuses_planted_address(page, chain):
    """The server lists the addresses to spend from; the frame re-derives them and refuses strangers."""
    import app as app_module
    real = app_module.wallet_addresses
    app_module.wallet_addresses = lambda *a: {**real(*a), "0x" + "77" * 20: 3}
    chain.balances[("ETH", "ETH", "0x" + "77" * 20)] = 10**18
    chain.balances[("ETH", "ETH", "0x9858EfFD232B4033E47d90003D41EC34EcaEda94")] = 0
    try:
        import_wallet(page)
        unlock(page)
        n = len(chain.relayed)
        row = page.locator("#balances li", has=page.locator(".network", has_text=re.compile("^Ethereum$")))
        row.get_by_role("button", name=re.compile("^Send")).click()
        page.fill("#send-to", TO_ETH)
        page.fill("#send-amount", "0.1")
        page.click("#send-review")
        expect(frame(page).locator("#working-error")).to_contain_text("address")
        assert len(chain.relayed) == n, "nothing signed or relayed"
    finally:
        app_module.wallet_addresses = real
