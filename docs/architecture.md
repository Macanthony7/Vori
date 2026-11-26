# Architecture

## Components

```
Browser (frontend/public)                               Server (backend/)
┌─────────────────────────────────────────────┐         ┌──────────────────────────────┐
│ index.html + app.js   (can reach our server) │  JSON   │ app.py (FastAPI)             │
│   │                                          │ ──────▶ │  /accounts  /payments        │
│   │ postMessage({type:"xpubs"})              │         │  /accounts/me/webhook        │
│ ┌─▼──────────────────────────────────────┐   │         │  serves frontend/public      │
│ │ derive.html  sandboxed iframe           │   │         └──────────────┬───────────────┘
│ │ - no network (CSP connect-src 'none')   │   │                        │
│ │ - opaque origin (sandbox=allow-scripts) │   │                 SQLite (gateway.db)
│ │ - phrase → BIP39 seed → 5 account xpubs │   │                        │
│ └─────────────────────────────────────────┘   │         ┌──────────────┴───────────────┐
│ xpub.html + app.js    (paste xpubs directly) │         │ watcher.py (separate process)│
└─────────────────────────────────────────────┘         │  chain_client → providers    │
                                                         │  payments.next_status()      │
Merchant's server ◀──── signed webhooks ──────────────── │  webhooks.deliver_due()      │
                                                         └──────────────────────────────┘
```

Two design rules:
1. **Code that can talk to the network never sees a recovery phrase, and code that sees the phrase can't talk to the network.** See [security.md](security.md).
2. **The server never holds anything that can move funds.** It only derives addresses from xpubs and reads balances.

## Onboarding flows

### Create a new wallet / import a phrase

```mermaid
sequenceDiagram
    participant U as Merchant
    participant P as index.html (app.js)
    participant F as derive.html (sandboxed, offline)
    participant S as Server
    U->>P: choose "Create" or "Import"
    P->>F: load derive.html#create or #import
    F->>U: show 12 new words / ask for phrase
    U->>F: confirm 3 words / paste phrase
    U->>F: choose a password
    F->>F: BIP39 seed → m/44'/coin'/account' xpubs; encrypt phrase → vault
    F->>F: wipe phrase and password from the page
    F->>P: postMessage {type: "xpubs", xpubs, account, vault}
    P->>S: POST /accounts {xpubs}
    S->>S: validate + normalize, store, hash API key
    S->>P: 201 {api_key, chains: {first_address}} (409 = already registered: same wallet, new device)
    P->>P: unload the frame, save vault in localStorage, show key + first addresses
```

- New wallets derive **account 0**: the wallet is dedicated to payments, so using the account every wallet shows is simplest.
- Imported phrases default to **account 1** (changeable under "Advanced") so payments stay separate from the wallet's everyday account 0. An optional BIP39 passphrase is supported.
- The frame resizes itself via `postMessage({type: "height"})` and reports load failures via `{type: "frame-error"}`.
- **Blocked-frame fallback:** the parent shows "Loading the secure phrase window…" until the frame's first message. If nothing arrives within 12 s, the browser blocked sandboxed scripts: the parent hides the frame and tells the user to switch browsers or use xpub keys. Known case: the Claude desktop app's preview browser, which never runs scripts in sandboxed iframes. Verified working in Chrome.

### Wallet unlock (`wallet.html`)

`wallet.js` reads the saved vault from `localStorage["wallet.vault.v1"]`, loads
`derive.html#unlock`, answers the frame's `ready` with the vault, and the frame asks for the
password. The unlocked keys stay in the frame (for signing Send, coming next). Lock = reload the
frame. Details and risks: [security.md](security.md#wallet-vault).

After unlock the page signs in without an API key: it asks the server for a challenge, the frame
signs it with the BTC main key, and `POST /auth/login` returns a session token kept in page memory.
The dashboard then shows live balances (`GET /wallet/balances`), Receive (the main address with a
QR code), recent payments, and "Make a new API key". Lock (button or the frame's 15-minute
auto-lock) ends the session and reloads the frame. See [api.md](api.md#wallet).

Merchants who signed up with xpub keys have no vault: their keys are in their own wallet.

### Send (`wallet.html`, BTC / LTC / DOGE)

```mermaid
sequenceDiagram
    participant P as wallet.js (online)
    participant F as derive.html (offline, unlocked)
    participant S as Server
    P->>S: GET /wallet/send/BTC
    S->>P: coins of every wallet address + fee rates
    P->>F: sign-send {to, amount, fee rate, coins}
    F->>P: engine-request (first time only)
    P->>F: engine files (frame checks SHA-256)
    F->>F: re-derive every listed address, plan, show confirm screen
    F->>P: send-signed {tx_hex} (after the merchant confirms)
    P->>S: POST /wallet/send/BTC {tx_hex}
    S->>P: {tx_hash} → explorer link
```

Details and checks: [security.md](security.md#sending).

### Use xpub keys

`xpub.html` posts the pasted xpubs straight to `POST /accounts`. Server validation errors come back per chain and are shown under the matching field.

### Checking the result

Every success response includes the first receive address (`m/44'/coin'/account'/0/0`) for each chain. The merchant compares it with their wallet, which catches a wrong account number or a wrong xpub before any money moves.

## Payments

```mermaid
sequenceDiagram
    participant M as Merchant server
    participant S as API (app.py)
    participant W as watcher.py
    participant C as Chain provider
    participant X as Customer
    M->>S: POST /payments {chain, asset, amount}
    S->>S: allocate address (next unused index)
    S->>M: {id, address, amount, expires_at, checkout_url}
    M->>X: redirect to checkout_url (/pay/{id})
    loop every 5 s
        X->>S: GET /pay/{id}/status (public, no key)
    end
    X->>C: sends funds to the merchant's own address
    loop every 10–60 s while open
        W->>C: observe(address): seen, confirmed
        W->>W: next_status() → update row
    end
    W->>M: webhook payment.confirming / confirmed / expired (signed)
```

### Address allocation (`payments.create`)

Inside one `BEGIN IMMEDIATE` transaction, so two requests can't take the same new index:

take `account_xpubs.next_index` and increment it. New accounts start at 1: index 0 is the main address (Receive, gas for Send).

Addresses are never reused. Earlier versions handed an unpaid address out again after 24 h, only
because other wallets stop scanning after ~20 unused addresses (gap limit). Our wallet doesn't
need that: it knows every address it handed out. Late coins are caught by the watcher for 24 h
after expiry and, after that, by the wallet's "Check all payment addresses" (`POST /wallet/scan`).
Merchants who restore the phrase in another wallet raise its gap limit ([api.md](api.md#address-assignment)).

### Watcher (`watcher.py`)

One pass (`tick`) every 5 s:
1. Load payments whose `next_check_at` has passed: open ones, plus `expired` ones for 24 h after expiry (late funds, checked every 15 min, `payments.LATE_WATCH` / `LATE_CHECK`).
2. `chain_client.observe()` returns `(seen, confirmed)` in smallest units.
3. `payments.next_status()`, a pure function, decides the new status (rules in [api.md](api.md#payment-statuses)).
4. If the amount went up, look up the incoming transaction hashes (`chain_client.tx_hashes`, best effort: a failure only loses the explorer link). Store amounts, hashes and status. On a change, queue a webhook event in the same transaction. On an expired payment, more confirmed funds queue `payment.paid_late`; amounts never go down there (the merchant may have sent the coins on).
5. `webhooks.deliver_due()` sends queued events and schedules retries.

Per-chain pacing (`payments.CHECK_EVERY`): BTC/LTC/DOGE 60 s (BlockCypher rate limits), ETH 15 s, BSC and TRX 10 s. A provider error never changes a payment's status. The payment is simply retried on schedule.

Only one watcher runs per database: `single_instance()` holds an OS file lock
(`<db>.watcher.lock`), released automatically even on a crash. It can run as a service, or from
cron with `--max-seconds` on hosts that kill long jobs. SIGTERM finishes the current pass before
exiting. See [setup.md](setup.md#run-locally).

### How "received" is measured (`chain_client.py`)

| Chain | Provider | Seen | Confirmed |
|---|---|---|---|
| BTC, LTC, DOGE | BlockCypher `/addrs/{a}` | Sum of outputs to the address, confirmed + unconfirmed | Outputs with ≥ N confirmations |
| ETH, BSC (native) | JSON-RPC `eth_getBalance` | Balance at `latest` | Balance at block `latest − N + 1` |
| ETH, BSC (USDT) | JSON-RPC `eth_call balanceOf` | Same, at `latest` | Same, at `latest − N + 1` |
| TRX (native) | TronGrid `getaccount` | `wallet/` (latest block) | `walletsolidity/` (irreversible) |
| TRX (USDT) | TronGrid `triggerconstantcontract balanceOf` | `wallet/` | `walletsolidity/` |

Reading the balance as of block `latest − N + 1` gives exactly the funds with at least N
confirmations, with no transaction scanning. Balance equals "received" because payment addresses
are never reused, and watching stops once a payment is final (24 h after expiry for `expired` ones).

## Key derivation

| Chain | Path | xpub prefix | Address type |
|---|---|---|---|
| BTC | `m/44'/0'/a'` | `xpub` | P2PKH (`1…`) |
| LTC | `m/44'/2'/a'` | `Ltub` (frame) / `xpub` (accepted) | P2PKH (`L…`) |
| DOGE | `m/44'/3'/a'` | `dgub` | P2PKH (`D…`) |
| ETH, BSC | `m/44'/60'/a'` | `xpub` | `0x…` (same address on both chains) |
| TRX | `m/44'/195'/a'` | `xpub` | `T…` |

Receive addresses are `…/0/index`. The server derives them with `bip_utils`. The browser derives
xpubs with the bitcoinjs bundled from Ian Coleman's tool. Both were checked against each other and
against that tool's test vectors.

## Data model

SQLite, in `db.py`. Every `*_at` column is UTC `YYYY-MM-DDTHH:MM:SSZ`, so strings sort by time.
Amounts are integers in smallest units, **stored as text**, because 100 ETH in wei doesn't fit in
SQLite's 64-bit integer.

```
accounts                              account_xpubs
- id                PK                - account_id   FK → accounts.id
- api_key_hash      UNIQUE (sha256)   - chain        BTC | LTC | DOGE | ETH | TRX
- plan, plan_expires_at               - xpub         normalized, sealed (AES-GCM)
                                      - xpub_tag     UNIQUE, keyed hash (lookup)
- created_at                          - next_index   next never-used address index
- webhook_url, webhook_secret (sealed) PK (account_id, chain)

payments                              webhook_events
- id                PK "pay_…"        - id               PK
- account_id        FK                - account_id, payment_id
- chain, asset, xpub_chain            - event            payment.confirming|confirmed|expired
- address_index, address              - body             exact signed JSON
- amount, received, received_confirmed (text ints)
- status            pending|confirming|confirmed|expired
- reference                           - attempts, next_attempt_at (NULL = done)
- created_at, expires_at, confirmed_at, updated_at, next_check_at
- tx_hashes         JSON list, oldest first
                                      - delivered_at, last_error

login_challenges                      sessions
- nonce             PK, single use    - token_hash       PK (sha256 of "gws_…")
- expires_at        5 min             - account_id       FK
                                      - expires_at       12 h
```

`db.init()` runs on API start and watcher start. It creates missing tables and adds columns
introduced after 0.1.0 (`MIGRATIONS`), so existing databases keep their accounts. WAL mode lets
the API and the watcher write at the same time.

## File map

| File | Role |
|---|---|
| `backend/app.py` | API routes, checkout route, wallet sign-in and balances, security headers, serves `../frontend/public` |
| `backend/db.py` | Connection, schema, migrations, timestamp helpers |
| `backend/xpubs.py` | `normalize()` (validate + fix version bytes) and `address()` |
| `backend/assets.py` | Supported chain/asset pairs, decimals, confirmations, token contracts, amount parsing |
| `backend/payments.py` | Address allocation, `next_status()` state machine, webhook queueing, API shape |
| `backend/chain_client.py` | Read-only provider queries (`observe` for payments, `balance` for the wallet) |
| `backend/webhooks.py` | URL safety checks, HMAC signing/verifying, delivery with retries |
| `backend/watcher.py` | Background loop tying the above together; single-instance lock, cron mode |
| `backend/test_xpubs.py`, `backend/test_payments.py` | Self-checks |
| `frontend/build_frame.py` | Inlines `frame/` + `public/styles.css` into `public/derive.html`, pins CSP hashes |
| `frontend/frame/derive.src.html`, `frontend/frame/derive.js` | Seed frame source: UI, derivation, vault encryption, unlock |
| `frontend/frame/sandbox-shim.js` | In-memory `localStorage` (sandboxed frames can't touch storage) + error reporting |
| `frontend/frame/vendor/` | Unmodified BIP39 engine from Ian Coleman's tool, with `SHA256SUMS` |
| `frontend/public/index.html`, `xpub.html`, `app.js`, `styles.css` | Onboarding pages |
| `frontend/public/wallet.html`, `wallet.js`, `wallet.css` | Wallet page: unlock, sign-in, balances, Receive, recent payments, new API key, lock, remove from this device |
| `frontend/public/pay.html`, `pay.js`, `pay.css` | Checkout page for payers, served at `/pay/{id}` |
| `frontend/public/vendor/wallet-core/` | Signing engine (Trust Wallet Core 4.8.7 + protobufjs minimal), unmodified, pinned by `SHA256SUMS`; see its `NOTICE.md` |
| `frontend/public/vendor/kjua.js` | QR codes (MIT), extracted unchanged from the vendored BIP39 bundle |
| `frontend/public/derive.html` | **Generated** seed frame (don't edit) |
| `frontend/public/tools/payment-xpub-tool.html` | Downloadable offline xpub tool (built from `xpub_tool/`) |
| `xpub_tool/` | Standalone offline xpub tool: Ian Coleman's BIP39 tool v0.5.6 with `payments:` changes |

## Relation to the old code

The project started as a custodial deposit system: a master `SEED_PHRASE` on the server, private
keys derived per deposit, funds swept to a main wallet. That code was removed (the last commit
containing it is `b18a440`). What was worth keeping (provider endpoints, the cron-friendly
single-instance watcher, transaction-hash lookups, monitoring queries) is either in the code or in
[roadmap.md](roadmap.md). `chain_client.py` uses the same providers (BlockCypher, Alchemy/Ankr,
TronGrid) with a smaller, corrected client. The old one read BSC USDT with 6 decimals instead of 18,
accepted 1% underpayments, and sent base58 Tron addresses where hex was required.
