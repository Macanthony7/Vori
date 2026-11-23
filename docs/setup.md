# Setup and deployment

## Requirements

- Python 3.11+ with `fastapi`, `uvicorn`, `bip_utils`, `requests`, `python-dotenv`. `httpx` is only needed for `test_payments.py`.
- No database server: SQLite ships with Python.

```powershell
pip install -r backend/requirements.txt
```

> On this machine `python` in Git Bash points at an unrelated venv. Use the Python that has these
> packages installed (the Windows Store Python 3.12 does).

## Run locally

Two processes, both run from `backend/`: the API, which also serves `frontend/public`, and the
watcher.

```powershell
cd backend
python -m uvicorn app:app --port 8000 --reload
```

```powershell
cd backend
python watcher.py
```

Open <http://localhost:8000>. API docs: <http://localhost:8000/api/docs>.

Watcher modes:

| Command | Use |
|---|---|
| `python watcher.py` | As a service (systemd, NSSM): loops until stopped. SIGTERM/Ctrl+C finishes the current pass first. |
| `python watcher.py --max-seconds 280` | **cPanel-style cron**: schedule every minute. It loops for up to 280 s and exits before a 5-minute limit. |
| `python watcher.py --once` | A single pass, for debugging. |

Only one watcher runs at a time. It holds an OS lock on `<database>.watcher.lock`, and extra
starts exit with "another watcher is running", so overlapping cron runs are harmless.

## Providers

The watcher reads balances from third-party providers. Copy `backend/.env.example` to `backend/.env` and
fill it in. Never commit `.env`.

| Variable | Used for | Notes |
|---|---|---|
| `BLOCKCYPHER_TOKEN` | BTC, LTC, DOGE | Optional. Without a token, BlockCypher allows ~100 requests/hour, enough for only a handful of payments. |
| `ETH_RPC_URL` | ETH, USDT-ERC20 | Full JSON-RPC URL with the key, e.g. `https://eth-mainnet.g.alchemy.com/v2/<key>` |
| `BSC_RPC_URL` | BNB, USDT-BEP20 | e.g. `https://rpc.ankr.com/bsc/<key>` |
| `TRONGRID_URL` | TRX, USDT-TRC20 | Default `https://api.trongrid.io` |
| `TRONGRID_API_KEY` | TRX, USDT-TRC20 | Optional, raises the rate limit |
| `GATEWAY_PUBLIC_URL` | API | Public base URL, used to build `checkout_url` (default `http://localhost:8000`). Set it in production. |
| `GATEWAY_DB` | all | SQLite path (default `backend/gateway.db`) |
| `GATEWAY_ALLOW_PRIVATE_WEBHOOKS` | webhooks | `1` = allow `http://` and private/loopback webhook URLs. **Development only.** |

The EVM endpoints must answer `eth_getBalance`/`eth_call` for recent past blocks (the last 15).
Every normal full node and the big providers do. Pruned "light" endpoints may not.

**Coming from the old `crypto_service/.env`:** `BLOCKCYPHER_API_KEY` becomes `BLOCKCYPHER_TOKEN`.
`ALCHEMY_API_KEY` becomes `ETH_RPC_URL=https://eth-mainnet.g.alchemy.com/v2/<key>`.
`ANKR_API_KEY` becomes `BSC_RPC_URL=https://rpc.ankr.com/bsc/<key>`. `TRONGRID_API_KEY` stays the
same. The old `AEGIS_SEED_PHRASE` and `MAIN_WALLET_*` are **not** used and must not be copied.

## Tests

From `backend/`:

```powershell
python test_xpubs.py
python test_payments.py
```

- `test_xpubs.py`: all 5 xpub families against reference addresses from Ian Coleman's BIP39 test suite. Also covers `Ltub`/`xpub` equivalence, and rejection of private keys, `zpub`, wrong depth, bad checksums and junk.
- `test_payments.py`: uses a throwaway database, a fake blockchain and a real local webhook receiver. It covers amount parsing, webhook URL rules and signatures, every API error, all status transitions (paid, underpaid, never confirmed, provider down), webhook retries, and the address-reuse rules.

**Live provider check** (read-only, public addresses). This was run against public no-key
endpoints on 2026-11-22, and all 9 chain/asset pairs parsed correctly:

```powershell
$env:ETH_RPC_URL="https://ethereum-rpc.publicnode.com"; $env:BSC_RPC_URL="https://bsc-rpc.publicnode.com"
python -c "from chain_client import observe; print(observe('TRX','USDT','TV6MuMXfmLbBqPZvBHdwFsDnQeVfnmiuSi',20))"
```

**Browser flows.** Use the test phrase
`abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about`
with account 0, and expect these first addresses:

| Chain | First address |
|---|---|
| BTC | `1LqBGSKuX5yYUonjxT5qGfpUsXKYYWeabA` |
| ETH/BSC | `0x9858EfFD232B4033E47d90003D41EC34EcaEda94` |
| TRX | `TUEZSdKsoDHQMeZwihtdoBiN46zxhGWYdH` |

That phrase is public, and its Tron address really does hold funds that strangers sent by mistake.
Never use it for anything real.

Create and import were verified in Chrome. The Claude desktop app's preview browser can't run them
(it blocks sandboxed frames) and shows the "browser blocked" message instead.

## Building the seed frame

`frontend/public/derive.html` is **generated**. Edit `frontend/frame/derive.src.html`,
`frontend/frame/derive.js`, `frontend/frame/sandbox-shim.js` or `frontend/public/styles.css`, then
run from `frontend/`:

```powershell
python build_frame.py
```

The script inlines everything, writes the SHA-256 CSP hashes into the page, and writes
`frontend/public/derive.html.sha256`. If you edit `derive.html` by hand, the hashes stop matching
and the browser refuses to run the frame's script.

## Updating the vendored BIP39 engine

1. Take the files from a tagged release of `iancoleman/bip39` (`src/js/`).
2. Replace them in `frontend/frame/vendor/` and regenerate `frontend/frame/vendor/SHA256SUMS`.
3. Run `python build_frame.py` in `frontend/`, then repeat the browser checks above.

## Offline tool

`frontend/public/tools/payment-xpub-tool.html` is the single-file offline tool built from
`xpub_tool/` (`python compile.py` there, which writes `xpub_tool/dist/`). Copy the new build and
its `.sha256` into `frontend/public/tools/` whenever it changes.

`xpub_tool/` is a vendored copy of `https://github.com/iancoleman/bip39` at commit
`de71c22328b24e0848bbe1bd12ac8974ca83b5b8` (v0.5.6), with our changes marked `payments:`. Diff
against that commit to review them.

## Deployment checklist

- [ ] Serve over **HTTPS only**. `crypto.getRandomValues` and the clipboard need a secure context, and the security model assumes the HTML isn't tampered with in transit.
- [ ] Run the API behind a reverse proxy (nginx or Caddy), from `backend/`: `uvicorn app:app --host 127.0.0.1 --port 8000 --workers 1`. Use a single worker while on SQLite. Deploy `frontend/` next to `backend/`, because the API serves `../frontend/public`.
- [ ] Run the watcher as a service (`python watcher.py`), or from cron every minute with `--max-seconds 280` on hosts that kill long jobs (cPanel). The lock keeps it to one instance.
- [ ] Fill in `backend/.env` with paid provider keys (see [Providers](#providers)) and set `GATEWAY_PUBLIC_URL` to the HTTPS address customers will open.
- [ ] If the proxy serves `frontend/public/` itself, copy the security headers from `backend/app.py` (`frame-ancestors 'self'`, `nosniff`, `no-referrer`).
- [ ] Never set `GATEWAY_ALLOW_PRIVATE_WEBHOOKS` in production.
- [ ] Add rate limiting on `POST /accounts`, `POST /payments` and `GET /pay/{id}/status`.
- [ ] Back up `backend/gateway.db`. It holds xpubs, API key hashes, webhook secrets and payment history.
- [ ] On shared cPanel hosting, the watcher works from cron (`* * * * * cd /path/backend && python watcher.py --max-seconds 280`). The API is the harder part: cPanel's "Setup Python App" runs WSGI, and FastAPI is ASGI, so it needs the `a2wsgi` adapter. A small VPS is simpler.
