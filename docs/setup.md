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
| `BLOCKCYPHER_TOKEN` | BTC, LTC, DOGE | Optional. Without a token, BlockCypher allows ~100 requests/hour, enough for only a handful of payments. With a token and an `https://` `GATEWAY_PUBLIC_URL`, BTC/LTC/DOGE payments also get push hooks, so they're polled every 10 min instead of every 60 s ([roadmap.md](roadmap.md#blockcypher-push-hooks)). |
| `GATEWAY_BLOCKCYPHER_HOOKS` | watcher | `0` = no push hooks even with a token (polling only). |
| `ETH_RPC_URL` | ETH, USDT-ERC20 | Full JSON-RPC URL with the key, e.g. `https://eth-mainnet.g.alchemy.com/v2/<key>` |
| `BSC_RPC_URL` | BNB, USDT-BEP20 | e.g. `https://rpc.ankr.com/bsc/<key>` |
| `TRONGRID_URL` | TRX, USDT-TRC20 | Default `https://api.trongrid.io` |
| `TRONGRID_API_KEY` | TRX, USDT-TRC20 | Optional, raises the rate limit |
| `NILE_TRONGRID_URL` | Nile | Default `https://nile.trongrid.io`. Only used with `GATEWAY_TESTNETS=1`; the TronGrid key is never sent to it. |
| `SEPOLIA_RPC_URL` | Sepolia | Any Sepolia JSON-RPC URL, e.g. `https://ethereum-sepolia-rpc.publicnode.com`. Only used with `GATEWAY_TESTNETS=1`. |
| `GATEWAY_TESTNETS` | all | `1` = add the Sepolia test network to payments, balances and sending. **Development only:** test coins have no value. |
| `GATEWAY_PUBLIC_URL` | API | Public base URL, used to build `checkout_url` (default `http://localhost:8000`). Set it in production. |
| `GATEWAY_DB` | all | SQLite path (default `backend/gateway.db`) |
| `GATEWAY_DB_KEY` | API, watcher | 64 hex characters. Encrypts xpubs and webhook secrets in the database ([security.md](security.md#database)). Generated into `backend/.env` on first start if missing. In production set it yourself (`python -c "import secrets; print(secrets.token_hex(32))"`) and **back it up apart from `gateway.db`**: without it the stored xpubs can't be read. |
| `GATEWAY_TRUST_PROXY` | API | `1` = read the visitor's IP for [rate limits](api.md#rate-limits) from the last `X-Forwarded-For` entry. Set it only behind a reverse proxy you run that sets that header (nginx: `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;`), and don't expose the app port directly: otherwise visitors could choose their own IP. |
| `GATEWAY_ALLOW_PRIVATE_WEBHOOKS` | webhooks | `1` = allow `http://` and private/loopback webhook URLs. **Development only.** |

The EVM endpoints must answer `eth_getBalance`/`eth_call` for recent past blocks (the last 15).
Every normal full node and the big providers do. Pruned "light" endpoints may not.

**Coming from the old `crypto_service/.env`:** `BLOCKCYPHER_API_KEY` becomes `BLOCKCYPHER_TOKEN`.
`ALCHEMY_API_KEY` becomes `ETH_RPC_URL=https://eth-mainnet.g.alchemy.com/v2/<key>`.
`ANKR_API_KEY` becomes `BSC_RPC_URL=https://rpc.ankr.com/bsc/<key>`. `TRONGRID_API_KEY` stays the
same. The old `AEGIS_SEED_PHRASE` and `MAIN_WALLET_*` are **not** used and must not be copied.

## Testing with Sepolia (test ETH)

The whole loop (checkout payment → balance → send) can be tried with free test ETH.

1. In `backend/.env`: `GATEWAY_TESTNETS=1` and `SEPOLIA_RPC_URL=…` (see `.env.example`). Restart the API.
2. Run the watcher in a terminal (`cd backend`, `python watcher.py`). Without it, payments are never detected.
3. Get test ETH from a Sepolia faucet into any wallet you control (MetaMask on Sepolia works).
4. **Create a payment:** wallet page → "New payment (checkout page)" → amount, "ETH on Sepolia" →
   Create checkout → Open the checkout page. Each payment gets a fresh address. Or with your API key:
   ```powershell
   $h = @{ Authorization = "Bearer gw_YOUR_KEY" }
   Invoke-RestMethod -Method Post http://localhost:8001/payments -Headers $h -ContentType "application/json" -Body '{"chain":"SEPOLIA","asset":"ETH","amount":"0.001"}'
   ```
   Open the returned `checkout_url` and pay it from your faucet wallet. With 3 confirmations
   (about 40 s) the page turns to "Paid".
5. **Gas for later:** on the wallet page, Receive → Sepolia shows your main address. Send a little
   test ETH there too.
6. **Send:** wallet page → Sepolia → Send. Funds on the payment address and the main address are
   sent as separate transfers, each paying its own fee; the confirm screen says how many.

The same switch also adds **Sepolia USDT** (Aave's faucet token: app.aave.com/faucet in testnet mode,
contract `0xaA8E…33D0`) and the **Tron Nile testnet** (TRX and USDT from nileex.io's faucet). For
USDT, first put some test ETH or TRX on your **main** address (Receive): it pays the gas and tops up
payment addresses automatically. The confirm screen says how many top-ups are sent first.

## Tests

Everything, from the repository root (details, layers and the process: [testing.md](testing.md)):

```powershell
python run_tests.py
```

The old self-checks still run on their own from `backend/`: `python test_xpubs.py`, `python test_payments.py`.

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

## Building the React pages

Every page (sign-up, xpub, wallet, checkout) is React + TypeScript in `frontend/app/`
([plan.md](plan.md#8-react-rebuild)); only the phrase frame is plain JS. The build in `frontend/public/app/` is committed, so the server
needs no Node. After changing anything in `frontend/app/` (or `frontend/public/styles.css`), run:

```powershell
cd frontend/app; npm ci; npm run build
```

`npm ci` installs the exact versions in `package-lock.json`; `.npmrc` turns off install scripts.
Add or update a package only to a release at least two weeks old. `python run_tests.py` fails if the
committed build doesn't match the sources.

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

## Updating the signing engine

`frontend/public/vendor/wallet-core/` holds Trust Wallet Core's browser build, unmodified. Its
`NOTICE.md` has the versions, the npm integrity hashes and the update steps. After replacing the
files, regenerate `SHA256SUMS` and rebuild the frame (`python build_frame.py`): the frame refuses
any engine file whose hash isn't the pinned one.

## Offline tool

`frontend/public/tools/payment-xpub-tool.html` is the single-file offline tool built from
`xpub_tool/` (`python compile.py` there, which writes `xpub_tool/dist/`). Copy the new build and
its `.sha256` into `frontend/public/tools/` whenever it changes.

`xpub_tool/` is a vendored copy of `https://github.com/iancoleman/bip39` at commit
`de71c22328b24e0848bbe1bd12ac8974ca83b5b8` (v0.5.6), with our changes marked `payments:`. Diff
against that commit to review them.

## Deployment checklist

Step-by-step for one VPS, with these config files ready: [deploy.md](deploy.md).

- [ ] Serve over **HTTPS only**. `crypto.getRandomValues` and the clipboard need a secure context, and the security model assumes the HTML isn't tampered with in transit.
- [ ] Run the API behind a reverse proxy (nginx or Caddy), from `backend/`: `uvicorn app:app --host 127.0.0.1 --port 8000 --workers 1`. Use a single worker while on SQLite. Deploy `frontend/` next to `backend/`, because the API serves `../frontend/public`.
- [ ] Run the watcher as a service (`python watcher.py`), or from cron every minute with `--max-seconds 280` on hosts that kill long jobs (cPanel). The lock keeps it to one instance.
- [ ] Fill in `backend/.env` with paid provider keys (see [Providers](#providers)) and set `GATEWAY_PUBLIC_URL` to the HTTPS address customers will open.
- [ ] If the proxy serves `frontend/public/` itself, copy the security headers from `backend/app.py` (`frame-ancestors 'self'`, `nosniff`, `no-referrer`).
- [ ] Never set `GATEWAY_ALLOW_PRIVATE_WEBHOOKS` in production.
- [ ] Behind a reverse proxy, set `GATEWAY_TRUST_PROXY=1` (otherwise every visitor shares the proxy's rate limits). Run one API process: rate-limit counts are per process.
- [ ] Back up `backend/gateway.db` (encrypted xpubs and webhook secrets, API key hashes, payment history) **and, separately, `GATEWAY_DB_KEY`**. One without the other is useless, which is the point.
- [ ] On shared cPanel hosting, the watcher works from cron (`* * * * * cd /path/backend && python watcher.py --max-seconds 280`). The API is the harder part: cPanel's "Setup Python App" runs WSGI, and FastAPI is ASGI, so it needs the `a2wsgi` adapter. A small VPS is simpler.
