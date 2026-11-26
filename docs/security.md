# Security model

## Goals

1. The server never receives a recovery phrase, a private key, the wallet password or the encrypted vault.
2. The server stores nothing that can spend funds. A full database leak loses privacy, not money.
3. No personal data is collected.

## What the server holds

| Data | Sensitivity | Protection |
|---|---|---|
| Account xpubs | **Privacy-sensitive, can't spend.** They reveal every address and the full history of that wallet account. | Encrypted at rest ([Database](#database)); decrypted in memory only to derive addresses. Users are told to use a dedicated account or wallet. |
| API key | Credential | Only `sha256(key)` is stored. Keys are 256-bit random (`secrets.token_urlsafe(32)`), so a fast hash is enough. Replaceable from the wallet (`POST /accounts/me/api-key`). |
| Wallet sessions | Credential, 12 h | Only `sha256(token)` is stored. Created only by a valid signature from the account's BTC main key; deleted on Lock. |
| Webhook secret | Lets someone forge webhooks to that merchant | Encrypted at rest ([Database](#database)); the server decrypts it to sign. Rotated by calling `PUT /accounts/me/webhook` again. |
| Payments | Business data: amounts, addresses, merchant references | Only visible to the owning API key (`404` for others). |
| Provider keys (`backend/.env`) | Billing / rate limits, **not funds** | Read-only use. Redacted from logs (`chain_client._redact`). Never commit `.env`. |
| Plan, timestamps | Low | — |

There's no master seed, no `SEED_PHRASE`, no hot wallet and no signing code anywhere in the gateway.
Payments land directly in the merchant's wallet.

## Database

xpubs and webhook secrets are encrypted in `gateway.db` with **AES-256-GCM** (`db.seal` / `db.unseal`,
Python `cryptography`). The key is `GATEWAY_DB_KEY` (32 random bytes, hex) in the environment or
`backend/.env`, never in the database. Two subkeys are derived from it with HMAC-SHA256: one encrypts,
one makes `xpub_tag`, a keyed hash used to find an account by xpub at login and to refuse a duplicate
sign-up without storing the xpub in clear. Each value is bound to its column (AES-GCM additional data),
so a sealed xpub can't be passed off as a webhook secret. Values start with `v1:`; plaintext rows from
older databases are encrypted by `db.init()` on the next start.

What this protects: a copy of `gateway.db` alone (a leaked backup, a stolen disk image) no longer
reveals merchants' whole wallets or lets anyone forge webhooks. What it doesn't: payment addresses,
amounts and timestamps are still in clear (they're public on chain anyway, but a leak links them to an
account), and anyone who has both the database and the key (a full server compromise) can read
everything.

**Keep the key apart from the database backups, and back it up.** Without it the stored xpubs can't
be read: merchants couldn't log in or get new payment addresses until they sign up again. Funds are
never at risk either way: the gateway can't spend. If `GATEWAY_DB_KEY` is missing on first start, the
server generates one and appends it to `backend/.env` (start one process first, so the API and the
watcher don't each generate a different key). Rotating the key isn't built yet.

## Seed phrase isolation

Paths 1 and 2 of onboarding need a recovery phrase in the browser. It's confined to
`frontend/public/derive.html`:

| Control | Effect |
|---|---|
| `<iframe sandbox="allow-scripts">` (no `allow-same-origin`) | The frame gets an opaque origin. The parent page's scripts can't read its DOM, and it can't read storage or cookies. |
| Frame CSP `connect-src 'none'; form-action 'none'; default-src 'none'` | The frame can't make any request: no fetch/XHR/WebSocket, form posts, images or fonts. |
| Frame CSP `script-src 'sha256-…'` (built by `build_frame.py`) | Only the exact inlined script may run. A modified or injected script is refused. |
| Everything inlined | The frame loads no files, so there's no subresource to tamper with. |
| Only `{type: "xpubs", xpubs, account, vault}`, `{type: "ready"}`, `{type: "unlocked", account}`, `{type: "locked"}`, `{type: "height"}` and `{type: "frame-error", message}` are posted out | The phrase never crosses into network-capable code in plain text; `vault` is ciphertext (see [Wallet vault](#wallet-vault)). |
| Best-effort wipe after deriving, then the parent sets `src="about:blank"` | The phrase doesn't linger in the page. JavaScript can't guarantee memory wiping. |
| `sandbox-shim.js` swaps `localStorage` for an in-memory object | Nothing in the frame can persist. The encrypted vault is stored by the parent page instead. |
| Server header `frame-ancestors 'self'` | Only our own pages can embed our pages, so `postMessage(…, "*")` from the frame can only reach our parent page. |

The parent (`app.js`) only accepts messages whose `event.source` is the frame's window, and
validates that every xpub value is a string before posting it.

**What this doesn't protect against:** malware on the user's device, malicious browser extensions,
or a compromised server sending different HTML. The last one can be checked by comparing against
`frontend/public/derive.html.sha256`. Users who want zero trust should use path 3 with a hardware wallet,
or the offline tool on an air-gapped machine.

## Wallet vault

The wallet keeps the recovery phrase **on the merchant's device**, encrypted with a password they
choose at signup, like MetaMask or Solflare's web wallet. The server never sees the phrase, the
password or the vault.

```mermaid
sequenceDiagram
    participant P as Page (app.js / wallet.js, online)
    participant F as derive.html (sandboxed, offline)
    Note over F: Signup: phrase step, then password step
    F->>F: PBKDF2-SHA256(password, random salt, 600k) → AES-256-GCM key
    F->>F: encrypt {phrase, passphrase, account}
    F->>P: {type: "xpubs", xpubs, vault}
    P->>P: POST /accounts (xpubs only), then localStorage["wallet.vault.v1"] = vault
    Note over P,F: Later: wallet.html
    P->>F: load derive.html#unlock
    F->>P: {type: "ready"}
    P->>F: {type: "vault", vault}
    F->>F: password → decrypt (wrong password = GCM failure)
    F->>P: {type: "unlocked", account}
    Note over F: keys stay in the frame's memory; locks after 15 min
```

| Control | Effect |
|---|---|
| Encryption and decryption happen only inside the offline frame | The online page only ever holds ciphertext. It never sees the password or the phrase. |
| WebCrypto PBKDF2-SHA256, **600,000** iterations (OWASP 2023), random 16-byte salt, AES-256-GCM with a random 12-byte IV | No extra library. GCM rejects a wrong password and any modified vault. |
| Vault format v2 `{v: 2, kdf, iterations, salt, iv, data}`, modeled on Gem Wallet's keystore v4: every metadata field is authenticated as AES-GCM additional data; exactly these fields, a 16-byte salt, a 12-byte IV, data at most 4 KB, 100,000 to 10,000,000 iterations | Changing any field (say, lowering the iterations) fails exactly like a wrong password. A planted vault can't downgrade the key derivation or hang the page. v1 vaults (no authenticated metadata) still unlock and are re-encrypted as v2 on the spot. |
| The frame only accepts a vault from `window.parent` with our own origin | Another page or window can't feed it one. |
| Unlocked keys live only in the frame's memory | Locking (button, auto-lock after 15 min, or closing the page) drops them. Locking reloads the frame from a new URL (`derive.html?load=N#unlock`), which clears everything. Re-setting the same URL would only change the fragment and *not* reload, which is how keys once stayed in memory after Lock (fixed; guarded by an E2E test). |
| Password at least 8 characters | The password can't be reset; restoring needs the recovery phrase. |
| One wallet per browser profile | Creating or importing another one replaces it; the signup page warns when a wallet is already saved. |
| Importing a phrase whose xpubs are already registered (a second device) still saves the vault | Same wallet, new device. The page says "Wallet restored on this device". |

**Sign-in.** After unlock, the frame signs the server's login challenge with the key at
`m/44'/0'/account'/0/0` and hands the page only the signature and the BTC xpub. It signs only
challenges in the exact `gw-login:<random>` format, always as `sha256("Gateway login\n" + challenge)`.
That text prefix means the page can't trick the frame into signing a transaction hash. Challenges
are single use and expire after 5 minutes. The session token lives only in the page's memory.

**Risks that remain (as with any web wallet):**
- **Offline guessing.** Anyone who copies the vault (malware, a malicious extension, an XSS bug on
  our pages, a stolen unlocked laptop) can try passwords offline. 600k PBKDF2 iterations make each
  guess slow, but a weak password still falls. The UI suggests a few random words.
- **Code from our server.** The frame is downloaded on every visit. A compromised server could
  serve a frame that leaks the password once it's typed. The CSP hash only stops code injected
  into *our* frame, not a replaced frame. The published `derive.html.sha256` lets anyone check it.
  A browser extension or mobile app (installed once, updated through a store) closes this; see
  [roadmap.md](roadmap.md).
- **Clearing site data deletes the vault.** The funds are safe; the merchant restores from the phrase.

## Sending

The send steps follow Gem Wallet's: **the server prepares, the offline frame plans, shows and signs, the
server relays.**

1. `GET /wallet/send/{chain}` lists spendable coins (≥ 1 confirmation) on the main address and every
   payment address that ever received funds, plus BlockCypher's fee rates.
2. The page passes that list and the merchant's destination and amount to the frame.
3. The frame **re-derives the address for every listed index** from the phrase and refuses the whole
   request if any doesn't match. A wrong or malicious list can't make it sign for coins that aren't
   this wallet's. It also validates the destination for that coin.
4. The frame plans the transaction with the signing engine and shows its **own** confirm screen:
   amount, destination, network fee, total. Nothing is signed until the merchant confirms there.
5. Only the signed transaction leaves the frame; `POST /wallet/send/{chain}` relays it.

**Signing engine.** Trust Wallet Core 4.8.7 (WebAssembly), vendored unmodified in
`frontend/public/vendor/wallet-core/` (provenance in its `NOTICE.md`). It's loaded only when the
merchant sends. The page downloads the four files and hands them to the frame, which checks each
SHA-256 against values pinned in its own CSP-hashed script before running anything: JavaScript
from `blob:` URLs, and the WebAssembly through a local `fetch` replacement that serves only the
verified bytes. The frame's CSP gains `blob:` and `'wasm-unsafe-eval'`; `connect-src 'none'` is
unchanged, so it still can't reach the network.

**EVM specifics (ETH, BNB, Sepolia ETH).** The frame pins each chain's id itself (1, 56,
11155111) and never takes it from the server: the chain id decides which network a signature is
valid on. It only signs plain transfers with a 21,000 gas limit and refuses fee quotes above
10,000 gwei. Each address re-derived from the phrase must match the listed one; the confirm screen
shows "up to" the fee (the most each transfer can cost) and how many transfers there are, since
every funded address sends its own.

**Tron specifics (TRX, Nile).** The server supplies the latest block header, which a Tron
transaction must reference; a wrong header only makes the network reject it. The frame signs
transfers that expire after 20 minutes, reserves the network fee per address (bandwidth, or the
1.1 TRX for activating a new destination) and relays the signed protobuf (`raw_data` + signature).

**USDT (ERC-20 / TRC-20) and gas top-ups.** Token contracts and decimals are pinned in the frame per
chain, never taken from the server (USDT has 18 decimals on BSC, 6 elsewhere). The main address
(index 0, re-derived like every other) pays gas: for each payment address without enough
ETH/BNB/TRX, the frame signs a top-up from the main address worth that address's worst-case token
fee, then the token transfers. The server's gas/energy estimates are bounded (≤ 200,000 gas; Tron
fee limit ≤ 100 TRX) and get a 20–25% margin. If the main address can't cover all of it, the frame
says how much to add and signs nothing. The page relays top-ups first and the token transfers only
after every top-up has confirmed; if they don't confirm within 3 minutes, no token moves and Send
can simply be retried (the gas already delivered is then used). A wrong estimate from the server
can only make a transfer fail or leave more dust, never redirect funds.

Checked: Ethereum signing matches the EIP-155 reference transaction byte for byte; a Bitcoin
transaction with two inputs from two of our legacy addresses, and a Tron USDT transfer, were
verified by an independent Python check (signatures, sighash, outputs, txID); the same Bitcoin
transaction signed in the browser frame verified too. A Sepolia EIP-1559 transfer signed in the
browser frame with live nonce and fee data was decoded independently: chain id, nonce, fees, value
and the recovered sender all matched. With live Sepolia and Nile data, the browser frame signed a
Sepolia USDT send from two payment addresses (2 top-ups from the main address, nonces in order), a
Nile USDT send with an activating top-up and a Nile TRX transfer; an independent Python decoder
recovered every signer and matched chain id, nonces, gas, contract, recipient, amounts, Tron fee
limit, block reference and txID. The frame refused a planted address, a main address short of gas,
and a TRX amount that didn't leave room for activating a new destination.

## Input validation (server)

`xpubs.normalize()` is the only door for keys:
- Base58Check decode, exact 78-byte length, max 120 characters.
- Rejects private-key headers with an explicit "never share this" message.
- Rejects anything not depth 3 with a hardened child (`m/44'/coin'/account'`).
- Normalizes `Ltub`/`dgub`/`xpub` version bytes, then parses with `bip_utils` (valid curve point).
- Max 5 xpubs per request (pydantic).

## Payment integrity

| Risk | Control |
|---|---|
| Fake token with the same name ("USDT") | Only the hardcoded contracts in `assets.py` are queried. Other tokens are invisible to the gateway. |
| Underpayment | A payment needs `received ≥ amount`. There's no tolerance (the old system accepted 1% less). |
| Reorg / dropped transaction | "Confirmed" needs N confirmations (TRX: a solidified block). Until then the status is `confirming`, and it falls back if the funds disappear. |
| Floating-point rounding | Amounts are parsed from strings with `Decimal` and stored as integer smallest units (as text). Floats are rejected. |
| Provider outage looks like non-payment | A provider error never changes a status. Payments aren't expired while the chain can't be observed. |
| Payment attributed to the wrong order after address reuse | Reuse only for addresses that received exactly 0 on the same chain/asset, after a 24 h quarantine. Remaining risk: see known gaps. |
| Duplicate webhooks from concurrent watchers | Status updates are guarded (`WHERE status = old`), and only the writer that made the change queues the event. |

## Webhooks

- **Signed:** `Gateway-Signature: t=<unix>,v1=HMAC-SHA256(secret, "<t>.<body>")`. Merchants must verify it, with a 5-minute timestamp window against replays (see [api.md](api.md#webhooks)).
- **SSRF protection:** URLs must be `https://` without credentials, and **every** resolved IP must be public (`ipaddress.is_global`). That rejects loopback, private ranges, link-local and cloud metadata (`169.254.169.254`). The check runs when the URL is saved **and again before every send**. Redirects aren't followed. Timeout 10 s.
- `GATEWAY_ALLOW_PRIVATE_WEBHOOKS=1` turns these checks off for local testing. Never set it in production.

## Response headers

`X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `frame-ancestors 'self'` on
every response. `Cache-Control: no-store` on `/accounts*` and `/payments*`, so API keys, webhook secrets and payment
data don't sit in caches. Pages set
their own CSP via `<meta>`: `connect-src 'self'` for the onboarding pages, and nothing at all for
the frame.

## Known gaps

| Gap | Plan |
|---|---|
| Rate limits are in memory, per process ([api.md](api.md#rate-limits)) | Fine for one server. Several processes or servers: limit at the reverse proxy or in Redis. An attacker with many IPs can still sign up many accounts (5/hour each); add a CAPTCHA-free proof of work or invite codes if that happens. |
| Webhook DNS rebinding: a host could resolve to a public IP at the check and a private one at connect | Connect to the already-checked IP (pinning), or send webhooks through an egress proxy that blocks private ranges. |
| Coins sent to an expired payment more than 24 h late aren't noticed automatically | They're safe in the merchant's wallet. "Check all payment addresses" (`POST /wallet/scan`) finds them on demand. |
| On EVM/Tron, late funds arriving after the merchant already sent an expired payment's earlier coins on are missed until they exceed the old amount | Rare: an underpaid payment whose coins were sent on, then paid again. The coins are safe on the address. Fix by reading transaction lists instead of balances (roadmap: transaction hashes). |
| BlockCypher counts at most the 200 most recent transactions per address | Fine for payment addresses (one or a few transactions each). |
| Lost API key for xpub-only accounts (no phrase in our wallet) | Wallet accounts replace it after signing in. xpub-only accounts would need a signature from their own wallet; not built. |
| xpubs stored in plaintext | Encrypt at rest with a server-held key (protects against DB-only leaks). |
| SQLite, single process | Move to Postgres/MySQL when scaling out. |
| Sandboxed frame verified in Chrome only. The Claude desktop preview browser blocks scripts in all sandboxed iframes, so the page shows a "browser blocked" message there after 12 s. | Check Firefox, Safari and mobile browsers before launch. Never "fix" a blocking browser by dropping `sandbox`: that removes the phrase isolation. |
| The signing engine is ~21 MB (10.4 MB WebAssembly + 10.8 MB generated protobuf code for every chain Wallet Core knows) | Downloaded only on the first Send, then cached. A trimmed `core_proto.js` with only Bitcoin, Ethereum and Tron would cut most of it. |
| Send not yet tried with real funds | Try a small amount on each chain before launch. |
| `bip39-libs.js` is a 3.5 MB bundle with many unused coins | A trimmed bundle with only bitcoinjs, ethereumjs-util and bs58 would shrink the audit surface. |
