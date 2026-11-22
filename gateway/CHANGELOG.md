# Changelog

## Unreleased

### Added
- **Payments:** `POST /payments`, `GET /payments/{id}` and `GET /payments`, for BTC, LTC, DOGE, ETH, BNB, TRX and USDT on Ethereum, BSC and Tron. 30-minute payment window. Amounts are decimal strings.
- **Watcher** (`watcher.py`): detects payments with standard confirmations (BTC 2, LTC 6, DOGE 6, ETH 12, BSC 15, TRX 20/solidified). Statuses are `pending`, `confirming`, `confirmed` and `expired`. A provider outage never expires a payment.
- **Webhooks:** `PUT` and `DELETE /accounts/me/webhook`. Events `payment.confirming`, `payment.confirmed` and `payment.expired`, signed with HMAC-SHA256 (`Gateway-Signature`), retried for up to 24 h. SSRF protection on webhook URLs.
- Address reuse for the wallet gap limit: an address is reused only if it received nothing on the same chain/asset and was unused for 24 h.
- `.env.example`, `test_payments.py`, and docs for payments, webhooks, the watcher and providers.
- "Loading the secure phrase window…" status while the seed frame starts. If the browser blocks sandboxed scripts, the page now hides the empty frame after 12 s and tells the user to switch browsers or use xpub keys, instead of showing a blank box.

### Changed
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
