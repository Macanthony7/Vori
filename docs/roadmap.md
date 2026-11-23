# Roadmap and notes from the prototype

The gateway grew out of an earlier **custodial** deposit system (a master seed on the server,
per-user deposit addresses, sweeping to a main wallet). That code was removed from the repo. The
last commit that contains it is `b18a440`. This page keeps what's still useful from it.

## Plan

**The product: a self-custody wallet with merchant features built in.** Think of Trust Wallet with
a checkout page and an API.

| Side | Lives where | Parts |
|---|---|---|
| **Wallet** (the merchant controls it) | The merchant's device | Password-protected vault holding the recovery phrase; one balance per coin across all payment addresses; Receive; Send, signed on the device |
| **Merchant features** | Our server, which only ever sees xpubs | A fresh address per payment, checkout page, watcher, API keys, signed webhooks |

Every payment arrives on its own fresh address, but the merchant sees one balance per coin and
moves funds with **Send**, unlocked with their password like MetaMask or Solflare's web wallet.

Focus: **the engine first**. Revenue features wait until it's solid.

### Engine (in order)

| # | Item | Why | Notes |
|---|---|---|---|
| 1 | **Hosted checkout page** `/pay/{id}` | The payer never saw anything; the address only existed in an API response | **Done:** see [api.md](api.md#checkout-page). |
| 2 | **Wallet vault** | Send without retyping the phrase every time | **Done** (verified in Chrome): see [security.md](security.md#wallet-vault). The phrase is encrypted with the merchant's password inside the offline frame; the page stores only the ciphertext on this device. Unlock with the password; auto-locks. |
| 3 | **Wallet dashboard: Balance + Receive** | The merchant needs to see the money in one place | **Done:** see [api.md](api.md#wallet). Live balance per coin over the main address plus every payment address that ever received funds (no gap limit). Receive = the main address (index 0); payments start at index 1. Recent payments. Balances cached 60 s. |
| 4 | **Dashboard login without the API key** | The API key shouldn't need to live in the browser | **Done:** the unlocked vault signs a single-use challenge with the BTC main key; the server verifies it against the stored xpub and issues a 12 h session. Also gives **API key recovery** (`POST /accounts/me/api-key`). |
| 5 | **Send, signed on the merchant's device** | Moving funds without custody | **BTC/LTC/DOGE and native ETH/BNB done** (Sepolia test network for trying it; not yet tried with real funds): see [security.md](security.md#sending-btc-ltc-doge). Steps modeled on Gem Wallet (prepare → plan → confirm in the frame → sign → relay); signing engine Trust Wallet Core. Next: native TRX, then USDT (gas from the main address). See [Sending and gas](#sending-and-gas). **Required before launch for ETH, BSC and Tron:** other wallets don't show our payment addresses there. |
| 6 | **Transaction hash on payments** | Receipts, explorer links on the checkout page, support | See [Transaction hashes](#transaction-hashes-how-the-prototype-found-them). |
| 7 | **"Paid late" detection** | Funds arriving after `expired` are invisible today | Keep observing expired payments for 24 h and emit `payment.paid_late`. |
| 8 | **Stop reusing addresses** | Payers never see a repeated address | Reuse exists only because other wallets stop scanning after ~20 empty addresses. Once our wallet shows every address, drop it. Keep an opt-in for merchants who spend from another wallet. |
| 9 | Rate limiting | Abuse, provider quota | `POST /accounts` per IP, `POST /payments` per key, `GET /pay/{id}/status` per IP. |
| 10 | BlockCypher push hooks | Save BTC/LTC/DOGE quota | See [BlockCypher push hooks](#blockcypher-push-hooks). |
| 11 | Encrypt xpubs at rest | DB-only leaks | Server-held key. |

### Reference: Gem Wallet

[Gem Wallet](https://github.com/gemwalletcom/wallet) (open-source iOS/Android wallet) is the
reference design for the wallet side. It's **GPL-3.0**, so we use it for ideas only and copy no
code; signing uses Trust Wallet Core (Apache-2.0/MIT) instead. Taken so far: the keystore format
(authenticated metadata, strict fields), the send steps, and a confirm screen in the signing
context. Still to take: per-chain fee rules (EIP-1559 priorities, Tron energy/bandwidth and the
new-account cost), transaction tracking after broadcast, and passkey/biometric unlock (WebAuthn
PRF) in place of the password.

### Later: optional

| Item | Notes |
|---|---|
| Browser extension / mobile app | Safer home for the vault: the code is installed once instead of downloaded on every visit (see [security.md](security.md#wallet-vault)). How MetaMask, Trust Wallet and Solflare grew. |
| Forwarder contracts ("auto-sweep") | Only if merchants want funds to move without pressing Send. CREATE2 forwarders + a relayer + prepaid gas. Needs a contract audit. Not needed for the wallet model. |

### Later: revenue

| Item | Notes |
|---|---|
| Free-tier limits + prepaid plans | Count payments per account per month; plans paid through the gateway itself extend `plan_expires_at` (see [overview.md](overview.md#how-we-make-money)). |
| Checkout swaps ("pay with any coin") | The customer's wallet signs an aggregator swap (1inch / 0x / PancakeSwap) that delivers the merchant's asset to the payment address. Integrator fee = revenue. Non-custodial. Cross-chain only via a partner (ChangeNOW-style), which custodies briefly. |

## Sending and gas

There is **no background sweeper**. Funds stay on their payment addresses until the merchant
presses Send, and the wallet picks which addresses to spend from, like any HD wallet.

| Asset | What Send does | Gas needed beforehand |
|---|---|---|
| BTC, LTC, DOGE | One transaction spending as many payment addresses as needed | None: the fee comes out of the coins being sent |
| ETH, BNB, TRX (native) | One transfer per address that holds funds | None: each address pays its own fee from its balance |
| USDT on Ethereum, BSC | **EIP-7702:** each payment address signs an authorization on the device; the merchant's main address sends one batch transaction and pays all the gas | Only on the merchant's **main** address |
| USDT on Tron | The main address stakes TRX and **delegates energy** to the payment addresses, the wallet sends, then reclaims the energy | Only on the main address (TRX staked for energy) |

So for tokens, "sweeping" is a step inside Send, not a job on the server. The problem it solves:
a fresh payment address holds USDT but no ETH/BNB/TRX, so it can't pay its own transfer fee.

**Rejected: server-side sweeper with keys** (what the prototype did). Signing for a payment
address needs its private key. A server that does that holds the keys, which is custody: the
thing this product exists to avoid.

Before building item 5 for tokens, check:
- The exact Tron bandwidth cost for a payment address that has never sent anything.
- Which audited EIP-7702 delegate (batch executor) contract to use on Ethereum and BSC, rather than writing our own.

**Tell merchants:** if they import the same phrase into another wallet (Trust Wallet, Electrum),
that wallet may not show funds past its ~20-address scan limit. The money is safe; our wallet
shows it.

## Transaction hashes (how the prototype found them)

The gateway measures **amounts** (balances at a confirmed block), which is enough to decide
whether a payment is paid. It doesn't record **which transaction** paid it yet. The prototype did,
per chain:

| Chain | Method | Notes |
|---|---|---|
| BTC, LTC, DOGE | BlockCypher `/addrs/{address}`: each entry in `txrefs` already has `tx_hash` | Free: the gateway already fetches this response. Easiest to add first. |
| ETH (native) | Alchemy `alchemy_getAssetTransfers` with `toAddress`, `category: ["external"]` | Alchemy-specific. Plain JSON-RPC can't search native transfers. |
| ETH / BSC (USDT) | `eth_getLogs` with `address = token contract`, `topics = [Transfer, null, <address padded to 32 bytes>]`. Transfer topic: `0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef` | Limit `fromBlock` to the payment's creation block. The prototype used `0x0`, which providers reject or throttle. |
| BSC (native BNB) | Not possible with plain RPC | Needs BscScan's API or another indexer. |
| TRX (native) | TronGrid `GET /v1/accounts/{address}/transactions?only_to=true` | |
| TRX (USDT) | TronGrid `GET /v1/accounts/{address}/transactions/trc20?contract_address=<USDT>&only_to=true` | Check `token_info.address` and `to`. |

Explorer links the prototype used:

| Chain | Transaction URL prefix |
|---|---|
| BTC | `https://blockchair.com/bitcoin/transaction/` |
| LTC | `https://blockchair.com/litecoin/transaction/` |
| DOGE | `https://blockchair.com/dogecoin/transaction/` |
| ETH | `https://etherscan.io/tx/` |
| BSC | `https://bscscan.com/tx/` |
| TRX | `https://tronscan.org/#/transaction/` |

## BlockCypher push hooks

Instead of polling BTC/LTC/DOGE addresses every 60 s, BlockCypher can call us:
`POST https://api.blockcypher.com/v1/{btc|ltc|doge}/main/hooks?token=…` with
`{"event": "unconfirmed-tx", "address": "<address>", "url": "<our endpoint>"}`.

The hook should only tell the watcher to check that payment now. It must **never** be trusted as
proof of payment, because anyone can call our endpoint. The watcher's own observation stays the
source of truth. Delete the hook when the payment is final, because BlockCypher limits active hooks
per token.

## Operations: queries worth alerting on

```sql
-- Payments the watcher hasn't been able to check for 10+ minutes (provider down or watcher stopped)
SELECT id, chain, asset, status, next_check_at FROM payments
WHERE status IN ('pending', 'confirming') AND next_check_at < strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-10 minutes');

-- Webhooks that were given up on
SELECT account_id, payment_id, event, attempts, last_error FROM webhook_events
WHERE delivered_at IS NULL AND next_attempt_at IS NULL;

-- Payments stuck in confirming for more than an hour
SELECT id, chain, asset, received, received_confirmed, updated_at FROM payments
WHERE status = 'confirming' AND created_at < strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-1 hour');
```

## Lessons from the prototype (already fixed in the gateway)

- **Decimals per token, not per symbol.** USDT is 6 decimals on Ethereum and Tron but 18 on BSC. The prototype assumed 6 everywhere.
- **No float money.** The prototype compared float balances with a `0.00000001` fudge and accepted payments 1% short. The gateway uses integer smallest units from decimal strings, and the amount must be fully paid.
- **Tron contract calls take hex addresses.** The prototype passed base58 to `balanceOf`.
- **Never scan from block 0.** Bound log queries by block range.
- **A provider error isn't "not paid".** The prototype could expire a session while the provider was down. The gateway never changes status without an observation.
- **One poller at a time.** The prototype used a file lock, kept in `watcher.py` (`single_instance`), along with its cron-friendly time-boxed loop (`--max-seconds`).
