# Changelog

## Unreleased

### Repository
- New layout: `frontend/` (pages + seed frame), `backend/` (API, watcher, tests), `xpub_tool/`, `docs/`. The old custodial prototype (seed on server, sweeping, HYIP integration) was removed. The last commit containing it is `b18a440`. What was worth keeping is in the code or in `docs/roadmap.md`.
- `xpub_tool/` is now plain files instead of a nested git clone (upstream commit recorded in `docs/setup.md`).
- `.gitignore` keeps secrets (`.env`, archives), databases, logs and caches out of git.
- `backend/requirements.txt` replaces the old root `requirements.txt`.

### Added
- **Send TRX and USDT** (Ethereum, BNB Smart Chain, Tron). Gas for USDT comes from the main address: the frame signs a top-up to each payment address that lacks gas plus the token transfers, and the page relays the token transfers once the top-ups have confirmed (new `GET /wallet/tx/{chain}/{hash}`). Token contracts and decimals are pinned in the frame. `GET /wallet/send/{chain}` takes `asset` and `to`.
- **Test networks:** Tron Nile (TRX, USDT) and Sepolia USDT, behind `GATEWAY_TESTNETS=1`.
- **New payment form** on the wallet dashboard: pick an amount and a coin/network, get a fresh address and a link to its checkout page (`POST /payments` with the wallet session).
- **Send native ETH and BNB** (and Sepolia ETH). Each funded wallet address sends its own transfer and pays its own fee; the frame pins chain ids, signs only 21,000-gas transfers and shows "up to" fees and the number of transfers. EIP-1559 fees from `eth_feeHistory` (BSC: legacy gas price). `POST /wallet/send/{chain}` now takes `{"txs": [...]}` and reports a part-way rejection.
- **Sepolia test network** behind `GATEWAY_TESTNETS=1` (`SEPOLIA_RPC_URL`): checkout payments, watcher, balances and sending, for testing the whole loop with free test ETH. See `docs/setup.md#testing-with-sepolia-test-eth`.
- **Send BTC, LTC and DOGE** from the wallet page, from the merchant's whole balance across all payment addresses. Steps modeled on Gem Wallet: `GET /wallet/send/{chain}` lists coins and fee rates, the offline frame re-derives every listed address, plans the transaction, shows its own confirm screen (amount, destination, fee, total) and signs; `POST /wallet/send/{chain}` relays it. "Send everything" and slow/normal/fast fees. Change returns to the main address.
- **Signing engine:** Trust Wallet Core 4.8.7 (WebAssembly), vendored unmodified in `frontend/public/vendor/wallet-core/`, loaded on the first Send and checked against SHA-256 values pinned in the frame. Verified against the EIP-155 reference and an independent check of Bitcoin and Tron signatures.
- **Vault v2:** metadata authenticated as AES-GCM additional data, strict format and size limits (after Gem Wallet's keystore v4). v1 vaults are upgraded on unlock.

- **Wallet dashboard** on `wallet.html` after unlock: live balance per coin (main address plus every payment address that ever received funds), Receive with QR code, recent payments, and "Make a new API key". New `GET /wallet/balances` and `chain_client.balance()`.
- **Sign-in without an API key:** `POST /auth/challenge`, `POST /auth/login` (ECDSA signature by the wallet's BTC main key, verified against the stored xpub), `DELETE /auth/session`. Sessions last 12 h and work on every authenticated endpoint. **API key recovery:** `POST /accounts/me/api-key`.
- Address index 0 is now each account's main address (Receive, gas for Send). New accounts' payments start at index 1. Existing accounts are unchanged.
- **Wallet vault.** After creating or importing a phrase, the merchant chooses a password. The offline frame encrypts the phrase (PBKDF2-SHA256 600k → AES-256-GCM, WebCrypto) and the page saves only that ciphertext on the device. New `wallet.html`: unlock with the password, auto-lock after 15 minutes, "Lock now", "Remove from this device". Importing a phrase that's already registered now restores the wallet on the new device instead of only showing an error. See `docs/security.md#wallet-vault`.
- **Checkout page** at `/pay/{id}` for payers: amount, network, QR code, address and amount with Copy, countdown, a wrong-network warning and live status (waiting, partly paid, confirming, paid, expired). The address is hidden once paying is no longer useful. Public data endpoint `GET /pay/{id}/status` (no API key, no `reference`). Payments now include `checkout_url`, built from the new `GATEWAY_PUBLIC_URL` setting.
- `backend/.env` is now loaded by every process (it was only read by the watcher's provider client).
- Roadmap direction: **a non-custodial wallet that is also a payment gateway.** No background sweeper and no forwarder contracts: the merchant sends from their own device, and token gas is paid from their main address (EIP-7702 on Ethereum/BSC, energy delegation on Tron). See `docs/roadmap.md`.
- Watcher cron mode, `--max-seconds N`, for hosts that kill long jobs (cPanel), plus a single-instance OS lock and graceful SIGTERM. Both were carried over from the prototype's poller.
- `docs/roadmap.md`: planned work, transaction-hash lookup methods per chain, explorer URLs, BlockCypher push hooks, monitoring queries, and lessons from the prototype.
- **Payments:** `POST /payments`, `GET /payments/{id}` and `GET /payments`, for BTC, LTC, DOGE, ETH, BNB, TRX and USDT on Ethereum, BSC and Tron. 30-minute payment window. Amounts are decimal strings.
- **Watcher** (`watcher.py`): detects payments with standard confirmations (BTC 2, LTC 6, DOGE 6, ETH 12, BSC 15, TRX 20/solidified). Statuses are `pending`, `confirming`, `confirmed` and `expired`. A provider outage never expires a payment.
- **Webhooks:** `PUT` and `DELETE /accounts/me/webhook`. Events `payment.confirming`, `payment.confirmed` and `payment.expired`, signed with HMAC-SHA256 (`Gateway-Signature`), retried for up to 24 h. SSRF protection on webhook URLs.
- Address reuse for the wallet gap limit: an address is reused only if it received nothing on the same chain/asset and was unused for 24 h.
- `.env.example`, `test_payments.py`, and docs for payments, webhooks, the watcher and providers.
- "Loading the secure phrase window…" status while the seed frame starts. If the browser blocks sandboxed scripts, the page now hides the empty frame after 12 s and tells the user to switch browsers or use xpub keys, instead of showing a blank box.

### Changed
- The secure frame now shrinks back after showing a taller step.
- Database setup moved to `db.py`. Existing databases are migrated automatically (new webhook columns and tables) and keep their accounts.

### Fixed (compared with the old custodial code's provider logic)
- USDT on BSC is read with 18 decimals (the old code assumed 6).
- No 1% underpayment tolerance.
- TRC20 balance queries send the hex address the contract call needs.

## 0.1.0 (2026-11-22)

First version of the non-custodial gateway, separate from the old custodial `crypto_service` code.

### Added
- Onboarding page with three paths: create a new wallet, import a recovery phrase, or paste xpub keys.
- Sandboxed, offline seed frame (`derive.html`) built by `build_frame.py`, with everything inlined and pinned by CSP hashes.
- `POST /accounts`: validates and normalizes account xpubs for BTC, LTC, DOGE, ETH/BSC and TRX, and returns a one-time API key.
- `GET /accounts/me`: API key check.
- `xpubs.py` validation (rejects private keys, ypub/zpub and wrong depth) and address derivation, with the `test_xpubs.py` self-check.
- Offline xpub tool download at `/tools/payment-xpub-tool.html`.
- Documentation: overview, architecture, API, security, setup.
