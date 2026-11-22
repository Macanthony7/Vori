# Roadmap and notes from the prototype

The gateway grew out of an earlier **custodial** deposit system (a master seed on the server,
per-user deposit addresses, sweeping to a main wallet). That code was removed from the repo. The
last commit that contains it is `b18a440`. This page keeps what's still useful from it.

## Planned

| Item | Why | Notes |
|---|---|---|
| Free-tier limits + prepaid plans | Revenue (see [overview.md](overview.md#how-we-make-money)) | Count payments per account per month; plans paid through the gateway itself extend `plan_expires_at`. |
| Rate limiting | Abuse, provider quota | `POST /accounts` per IP, `POST /payments` per key. |
| API key recovery | No email to reset | Sign a challenge with the key at `…/0/0`, verify against the stored xpub. |
| Transaction hash on payments | Receipts, explorer links, support | See below. |
| BlockCypher push hooks | Save BTC/LTC/DOGE quota | See below. |
| "Paid late" event | Funds arriving after `expired` are invisible today | Keep observing expired payments for 24 h. |
| Encrypt xpubs at rest | DB-only leaks | Server-held key. |

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
