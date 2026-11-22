# Payment gateway (non-custodial)

Accept crypto payments without collecting personal data and without ever holding funds or keys.
Merchants sign up with **public keys only** (account xpubs). Payments go straight to addresses in
the merchant's own wallet.

Supported: **BTC, LTC, DOGE, ETH, BNB (BSC), TRX**, and **USDT on Ethereum, BSC and Tron**.

## Status

| Part | State |
|---|---|
| Onboarding page: create wallet / import phrase / paste xpubs | Built; verified in Chrome |
| `POST /accounts`, `GET /accounts/me` | Built and tested |
| Payments: `POST /payments`, `GET /payments[/{id}]`, 30-min window, address reuse for the gap limit | Built and tested |
| Watcher: detection with standard confirmations, native coins + USDT | Built; logic tested with a fake chain, providers checked live (read-only) |
| Signed webhooks with retries | Built and tested |
| Free-tier limits, paid plans, rate limiting, API key recovery | Not started |

## Quick start (development)

```powershell
cd gateway
copy .env.example .env
python build_frame.py
python -m uvicorn app:app --port 8000 --reload
```

In a second terminal:

```powershell
cd gateway
python watcher.py
```

Open <http://localhost:8000>. API docs: <http://localhost:8000/api/docs>. Self-checks:
`python test_xpubs.py` and `python test_payments.py`. Provider keys and deployment notes are in
[docs/setup.md](docs/setup.md).

## Documentation

| Doc | What's in it |
|---|---|
| [docs/overview.md](docs/overview.md) | What the product is, the non-custodial model, how a payment works, glossary |
| [docs/architecture.md](docs/architecture.md) | Components, onboarding and payment flows, watcher, data model, file map |
| [docs/api.md](docs/api.md) | HTTP API reference, payment statuses, webhooks and signature verification |
| [docs/security.md](docs/security.md) | Threat model, seed isolation, payment integrity, webhook SSRF rules, known gaps |
| [docs/setup.md](docs/setup.md) | Development, providers, tests, building the seed frame, deployment |
| [CHANGELOG.md](CHANGELOG.md) | What changed, by version |

## Layout

```
gateway/
  app.py            FastAPI app: API + serves static/
  db.py             SQLite schema, migrations, timestamps
  xpubs.py          xpub validation and address derivation (public keys only)
  assets.py         chain/asset table: decimals, confirmations, USDT contracts, amount parsing
  payments.py       address allocation, status state machine, webhook queueing
  chain_client.py   read-only balance queries (BlockCypher, JSON-RPC, TronGrid)
  webhooks.py       URL safety, HMAC signing, delivery with retries
  watcher.py        background loop: python watcher.py
  test_*.py         self-checks
  build_frame.py    builds static/derive.html from frame/
  frame/            SOURCE of the sandboxed seed frame (never served directly)
  static/           served pages (index.html, xpub.html, app.js, styles.css, derive.html, tools/)
  docs/             documentation
  .env.example      provider configuration template
```

The BIP39 engine in `frame/vendor/` is taken unmodified from Ian Coleman's BIP39 tool (MIT,
see `frame/vendor/LICENSE-bip39-tool.txt`).
