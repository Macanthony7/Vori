# Security model

## Goals

1. The server never receives a recovery phrase or any private key.
2. The server stores nothing that can spend funds. A full database leak loses privacy, not money.
3. No personal data is collected.

## What the server holds

| Data | Sensitivity | Protection |
|---|---|---|
| Account xpubs | **Privacy-sensitive, can't spend.** They reveal every address and the full history of that wallet account. | Stored as-is (needed to derive addresses). Users are told to use a dedicated account or wallet. Encrypting at rest is a known gap. |
| API key | Credential | Only `sha256(key)` is stored. Keys are 256-bit random (`secrets.token_urlsafe(32)`), so a fast hash is enough. |
| Webhook secret | Lets someone forge webhooks to that merchant | Stored in plaintext, because the server needs it to sign. Rotated by calling `PUT /accounts/me/webhook` again. |
| Payments | Business data: amounts, addresses, merchant references | Only visible to the owning API key (`404` for others). |
| Provider keys (`backend/.env`) | Billing / rate limits, **not funds** | Read-only use. Redacted from logs (`chain_client._redact`). Never commit `.env`. |
| Plan, timestamps | Low | — |

There's no master seed, no `SEED_PHRASE`, no hot wallet and no signing code anywhere in the gateway.
Payments land directly in the merchant's wallet.

## Seed phrase isolation

Paths 1 and 2 of onboarding need a recovery phrase in the browser. It's confined to
`frontend/public/derive.html`:

| Control | Effect |
|---|---|
| `<iframe sandbox="allow-scripts">` (no `allow-same-origin`) | The frame gets an opaque origin. The parent page's scripts can't read its DOM, and it can't read storage or cookies. |
| Frame CSP `connect-src 'none'; form-action 'none'; default-src 'none'` | The frame can't make any request: no fetch/XHR/WebSocket, form posts, images or fonts. |
| Frame CSP `script-src 'sha256-…'` (built by `build_frame.py`) | Only the exact inlined script may run. A modified or injected script is refused. |
| Everything inlined | The frame loads no files, so there's no subresource to tamper with. |
| Only `{type, xpubs, account}`, `{type: "height"}` and `{type: "frame-error", message}` are posted out | The phrase never crosses into network-capable code. |
| Best-effort wipe after deriving, then the parent sets `src="about:blank"` | The phrase doesn't linger in the page. JavaScript can't guarantee memory wiping. |
| `sandbox-shim.js` swaps `localStorage` for an in-memory object | Nothing in the frame can persist. |
| Server header `frame-ancestors 'self'` | Only our own pages can embed our pages, so `postMessage(…, "*")` from the frame can only reach our parent page. |

The parent (`app.js`) only accepts messages whose `event.source` is the frame's window, and
validates that every xpub value is a string before posting it.

**What this doesn't protect against:** malware on the user's device, malicious browser extensions,
or a compromised server sending different HTML. The last one can be checked by comparing against
`frontend/public/derive.html.sha256`. Users who want zero trust should use path 3 with a hardware wallet,
or the offline tool on an air-gapped machine.

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
| No rate limiting on `POST /accounts` or `POST /payments` | Add per-IP / per-key limits before a public launch (reverse proxy or middleware). Unlimited payment creation also burns provider quota. |
| Webhook DNS rebinding: a host could resolve to a public IP at the check and a private one at connect | Connect to the already-checked IP (pinning), or send webhooks through an egress proxy that blocks private ranges. |
| A late payment (more than 24 h after expiry) to a reused address would be credited to the new payment | Rare: the customer pays a day late, to an address nothing else paid. Could be closed by checking the address balance at reuse time. |
| Funds arriving after `expired` aren't tracked | They're safe in the merchant's wallet. A "paid late" event would make them visible. |
| BlockCypher counts at most the 200 most recent transactions per address | Fine for payment addresses (one or a few transactions each). |
| Lost API key = lost account | Recovery by signing a challenge with the key at `…/0/0`, verified against the stored xpub. |
| xpubs stored in plaintext | Encrypt at rest with a server-held key (protects against DB-only leaks). |
| SQLite, single process | Move to Postgres/MySQL when scaling out. |
| Sandboxed frame verified in Chrome only. The Claude desktop preview browser blocks scripts in all sandboxed iframes, so the page shows a "browser blocked" message there after 12 s. | Check Firefox, Safari and mobile browsers before launch. Never "fix" a blocking browser by dropping `sandbox`: that removes the phrase isolation. |
| `bip39-libs.js` is a 3.5 MB bundle with many unused coins | A trimmed bundle with only bitcoinjs, ethereumjs-util and bs58 would shrink the audit surface. |
