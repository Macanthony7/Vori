# Payment gateway (non-custodial)

Accept crypto payments without collecting personal data and without ever holding funds or keys.
Merchants sign up with **public keys only** (account xpubs). Payments go straight to addresses in
the merchant's own wallet.

Supported: **BTC, LTC, DOGE, ETH, BNB (BSC), TRX**, and **USDT on Ethereum, BSC and Tron**.

## Repository layout

```
frontend/     onboarding pages + the sandboxed seed frame (served by the backend)
backend/      API (FastAPI), payment watcher, database, tests
xpub_tool/    standalone offline xpub tool (modified copy of Ian Coleman's BIP39 tool)
docs/         documentation
```

```
frontend/
  public/           served as-is: index.html, xpub.html, app.js, styles.css,
                    derive.html (GENERATED), tools/ (offline xpub tool download)
  frame/            SOURCE of the seed frame: derive.src.html, derive.js, sandbox-shim.js,
                    vendor/ (unmodified BIP39 engine, MIT, with SHA256SUMS)
  build_frame.py    builds public/derive.html from frame/
backend/
  app.py            API routes; serves ../frontend/public
  db.py             SQLite schema, migrations, timestamps
  xpubs.py          xpub validation and address derivation (public keys only)
  assets.py         chain/asset table: decimals, confirmations, USDT contracts, amount parsing
  payments.py       address allocation, status state machine, webhook queueing
  chain_client.py   read-only balance queries (BlockCypher, JSON-RPC, TronGrid)
  webhooks.py       URL safety, HMAC signing, delivery with retries
  watcher.py        background loop: python watcher.py
  test_*.py         self-checks
  .env.example      provider configuration template
  requirements.txt
```

## Status

| Part | State |
|---|---|
| Onboarding page: create wallet / import phrase / paste xpubs | Built; verified in Chrome |
| `POST /accounts`, `GET /accounts/me` | Built and tested |
| Payments: `POST /payments`, `GET /payments[/{id}]`, 30-min window, address reuse for the gap limit | Built and tested |
| Watcher: detection with standard confirmations, native coins + USDT; cron mode with a single-instance lock | Built; logic tested with a fake chain, providers checked live (read-only) |
| Signed webhooks with retries | Built and tested |
| Wallet vault: password-encrypted phrase on the device, unlock / auto-lock (`wallet.html`) | Built; logic tested, verified in Chrome |
| Checkout page `/pay/{id}` with QR, countdown and live status | Built; API tested, page checked in the preview browser (desktop + mobile) |
| Wallet dashboard: live balances, Receive, recent payments, sign-in by signature, API key recovery | Built and tested; checked in the preview browser (signatures from the real frame code verified by the server) |
| Send BTC / LTC / DOGE, signed in the offline frame (Trust Wallet Core) | Built and tested; signatures verified independently; not yet tried with real funds or inside Chrome's sandboxed frame |
| Send native ETH / BNB, plus the Sepolia test network (`GATEWAY_TESTNETS=1`) | Built and tested; Sepolia transfer signed in the browser and verified independently; not yet broadcast |
| Send TRX / USDT, transaction hashes, paid-late, rate limiting | Planned, see [docs/roadmap.md](docs/roadmap.md) |
| Free-tier limits, paid plans | Later |

## Quick start (development)

```powershell
cd backend
pip install -r requirements.txt
copy .env.example .env
python -m uvicorn app:app --port 8000 --reload
```

In a second terminal:

```powershell
cd backend
python watcher.py
```

Open <http://localhost:8000>. API docs: <http://localhost:8000/api/docs>.

Self-checks, run from `backend/`: `python test_xpubs.py` and `python test_payments.py`.
After editing the seed frame, run `python build_frame.py` from `frontend/`.
Provider keys and deployment notes are in [docs/setup.md](docs/setup.md).

## Documentation

| Doc | What's in it |
|---|---|
| [docs/overview.md](docs/overview.md) | What the product is, the non-custodial model, how a payment works, glossary |
| [docs/architecture.md](docs/architecture.md) | Components, onboarding and payment flows, watcher, data model, file map |
| [docs/api.md](docs/api.md) | HTTP API reference, payment statuses, webhooks and signature verification |
| [docs/security.md](docs/security.md) | Threat model, seed isolation, payment integrity, webhook SSRF rules, known gaps |
| [docs/setup.md](docs/setup.md) | Development, providers, tests, building the seed frame, deployment |
| [docs/roadmap.md](docs/roadmap.md) | What's next, plus what was kept from the original custodial prototype |
| [CHANGELOG.md](CHANGELOG.md) | What changed, by version |
