# Provider Setup Guide - 4 Provider Architecture with Dedicated Poller

This guide walks through setting up the 4 blockchain API providers used by Aegis Crypto Service.

## Architecture Overview

| Chain | Provider | Purpose |
|-------|----------|---------|
| BTC, LTC, DOGE | **BlockCypher** | UTXO transaction verification, balance, UTXO fetching, broadcast |
| ETH, ERC20 (USDT, USDC, DAI) | **Alchemy** | ETH native + ERC20 token verification, balance, gas, broadcast |
| BSC, BEP20 (USDT, USDC, BUSD) | **Ankr** (primary) + **BscScan** (fallback) | BNB native + BEP20 token verification, balance, gas, broadcast |
| TRX, TRC20 (USDT, USDC) | **TronGrid** | TRX native + TRC20 token verification, balance, energy, broadcast |

---

## System Architecture: Dual Detection Paths

The system uses **two detection paths** for maximum reliability:

### Path 1: Webhook (Primary - Real-time)
- Providers send webhooks to Laravel on transaction confirmation
- Handled by `webhook_processor.php`
- Near-instant detection

### Path 2: Dedicated Poller (Secondary - 10-sec intervals)
- **NEW**: `poller.py` runs 30×10-second loops per cron execution (5 min max)
- Queries active `pending` sessions every 10 seconds
- Routes balance checks to correct provider per chain
- Calls Laravel webhook (`/webhook/aegis`) to credit user when confirmed
- Auto-expires sessions after 15 minutes

### Path 3: Fallback Watcher (Tertiary - 5-min cron)
- `fallback_watcher.py` catches any missed webhooks/polls
- Runs every 5 minutes via cron
- Uses same provider routing for transaction hash discovery

---

## 1. BlockCypher (BTC, LTC, DOGE)

### Setup Steps

1. **Create Account**: Go to https://www.blockcypher.com/ and sign up
2. **Get API Token**: Navigate to Dashboard → API Tokens → Create Token
3. **Configure Webhooks** (for real-time payment detection):
   - In BlockCypher dashboard: Webhooks → Add Webhook
   - URL: `https://yourdomain.com/webhook/blockcypher`
   - Events: `confirmed`, `unconfirmed`
   - The `address` filter will be set dynamically per payment address

### API Endpoints Used
- Transaction: `GET https://api.blockcypher.com/v1/{coin}/main/txs/{tx_hash}?token={TOKEN}`
- Address Balance: `GET https://api.blockcypher.com/v1/{coin}/main/addrs/{address}?token={TOKEN}`
- UTXOs: `GET https://api.blockcypher.com/v1/{coin}/main/addrs/{address}?unspentOnly=true&token={TOKEN}`
- Create TX: `POST https://api.blockcypher.com/v1/{coin}/main/txs/new?token={TOKEN}`
- Send TX: `POST https://api.blockcypher.com/v1/{coin}/main/txs/send?token={TOKEN}`

### Chain Mapping
- BTC → `btc`
- LTC → `ltc`
- DOGE → `doge`

---

## 2. Alchemy (ETH, ERC20)

### Setup Steps

1. **Create Account**: Go to https://www.alchemy.com/ and sign up
2. **Create App**: Dashboard → Create App → Ethereum → Mainnet
3. **Get API Key**: Copy the HTTPS URL (e.g., `https://eth-mainnet.g.alchemy.com/v2/xxxxx`)
3. **Enable Enhanced APIs**: Ensure "Enhanced APIs" is enabled in app settings (required for `alchemy_getAssetTransfers`)
4. **Configure Webhook** (for real-time payment detection):
   - In Alchemy dashboard: Notify → Create Webhook
   - URL: `https://yourdomain.com/webhook/alchemy`
   - Type: `Address Activity` (for specific addresses) or `Mined Transaction`

### API Endpoints Used
- Transaction Receipt: `eth_getTransactionReceipt`
- Transaction Details: `eth_getTransactionByHash`
- Block Number: `eth_blockNumber`
- Balance: `eth_getBalance`
- Token Balance: `eth_call` to `balanceOf(address)`
- Gas Price: `eth_gasPrice`
- Nonce: `eth_getTransactionCount`
- Estimate Gas: `eth_estimateGas`
- Broadcast: `eth_sendRawTransaction`
- **Transaction History (Poller/Fallback)**: `alchemy_getAssetTransfers` (Enhanced API)

### Token Contract Addresses (from config/aegispayment.php)
- USDT: `0xdAC17F958D2ee523a2206206994597C13D831ec7`
- USDC: `0xA0b86a33E6441b8C4C8C8C8C8C8C8C8C8C8C8C8C8`
- DAI: `0x6B175474E89094C44Da98b954EedeAC495271d0F`

---

## 3. Ankr (BSC, BEP20)

### Setup Steps

1. **Create Account**: Go to https://www.ankr.com/rpc/ and sign up
2. **Get API Key**: Dashboard → API Keys → Create Key for BSC Mainnet
3. **RPC Endpoint**: `https://rpc.ankr.com/bsc/{ANKR_API_KEY}`
4. **Optional - BscScan API Key** (Recommended for fallback watcher transaction lookup):
   - Create account at https://bscscan.com/
   - Generate API key
   - Add to `.env`: `BSCSCAN_API_KEY=your_bscscan_key`

### Webhooks
- Ankr doesn't have native webhooks for BSC
- **Poller** (10-sec interval) and **Fallback Watcher** (5-min cron) handle BSC detection

### API Endpoints Used
- Same as Alchemy (EVM-compatible):
  - `eth_getTransactionReceipt`, `eth_getTransactionByHash`, `eth_blockNumber`
  - `eth_getBalance`, `eth_call` (balanceOf), `eth_gasPrice`, `eth_getTransactionCount`
  - `eth_estimateGas`, `eth_sendRawTransaction`
- **Fallback Watcher Transaction Lookup**:
  - Primary: BscScan API (`tokentx`/`txlist` endpoints)
  - Fallback: Ankr `eth_getLogs` with Transfer event filter

### Token Contract Addresses (from config/aegispayment.php)
- USDT: `0x55d398326f99059fF775485246999027B3197955`
- USDC: `0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d`
- BUSD: `0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56`

---

## 4. TronGrid (TRX, TRC20)

### Setup Steps

1. **Create Account**: Go to https://www.trongrid.io/ and sign up
2. **Create Project**: Dashboard → Create Project → Mainnet
3. **Get API Key**: Copy the API Key
4. **Configure Webhook** (for real-time payment detection):
   - In TronGrid dashboard: Events → Create Event Subscription
   - URL: `https://yourdomain.com/webhook/trongrid`
   - Contract Address: Add USDT/USDC contract addresses
   - Event: `Transfer`

### API Endpoints Used
- Transaction: `POST https://api.trongrid.io/wallet/gettransactionbyid`
- Account: `POST https://api.trongrid.io/wallet/getaccount`
- Current Block: `POST https://api.trongrid.io/wallet/getnowblock`
- Trigger Constant Contract: `POST https://api.trongrid.io/wallet/triggerconstantcontract` (for balanceOf)
- Estimate Energy: `POST https://api.trongrid.io/wallet/estimateenergy`
- Broadcast: `POST https://api.trongrid.io/wallet/broadcasttransaction`
- **Poller/Fallback Transaction Lookup (TronGrid v2)**:
  - Native TRX: `GET /v2/accounts/{address}/transactions`
  - TRC20: `GET /v2/accounts/{address}/transactions/trc20`
  - Params: `only_confirmed=true`, `limit=50`, `order_by=block_timestamp,desc`

### Token Contract Addresses (from config/aegispayment.php)
- USDT: `TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t`
- USDC: `TEkxiTehnzSmSe2XqrBj4w32RUN966rdz8`

---

## .env Configuration

### crypto_service/.env
```env
# Database
DB_HOST=127.0.0.1
DB_PORT=3306
DB_DATABASE=hyip
DB_USERNAME=root
DB_PASSWORD=

# Seed Phrase
AEGIS_SEED_PHRASE=your_twelve_word_seed_phrase_here

# Main Wallets
MAIN_WALLET_BTC=your_btc_address
MAIN_WALLET_ETH=your_eth_address
MAIN_WALLET_BSC=your_bsc_address
MAIN_WALLET_LTC=your_ltc_address
MAIN_WALLET_DOGE=your_doge_address
MAIN_WALLET_TRX=your_trx_address

# API Keys (4 Provider Architecture)
ALCHEMY_API_KEY=your_alchemy_key
BLOCKCYPHER_API_KEY=your_blockcypher_key
ANKR_API_KEY=your_ankr_key
TRONGRID_API_KEY=your_trongrid_key

# Optional: BscScan for BSC transaction hash lookup (fallback watcher)
BSCSCAN_API_KEY=your_bscscan_key

# App Key (Laravel APP_KEY for internal auth)
APP_KEY=base64:your_laravel_app_key

# Laravel Webhook Integration
AEGIS_WEBHOOK_URL=https://yourdomain.com/webhook/aegis
AEGIS_WEBHOOK_TIMEOUT=30
```

### hyip/.env (add to existing)
```env
# AegisPayment XPUB Keys (for address generation)
XPUB_BTC=xpub6...
XPUB_ETH=xpub6...
XPUB_LTC=xpub6...
XPUB_DOGE=dgub...
XPUB_BSC=xpub6...
XPUB_TRX=xpub6...

# Logo URL for payment pages
AEGIS_LOGO_URL=https://yourdomain.com/logo.png

# Aegis Crypto Service Webhook URL (for Python poller to notify Laravel)
AEGIS_WEBHOOK_URL=https://yourdomain.com/webhook/aegis
```

---

## Webhook URL Configuration

Each provider's webhook must point to the corresponding Laravel route:

| Provider | Webhook URL | Route Name |
|----------|-------------|------------|
| Alchemy | `https://yourdomain.com/webhook/alchemy` | `webhook.alchemy` |
| BlockCypher | `https://yourdomain.com/webhook/blockcypher` | `webhook.blockcypher` |
| Ankr | `https://yourdomain.com/webhook/ankr` | `webhook.ankr` |
| TronGrid | `https://yourdomain.com/webhook/trongrid` | `webhook.trongrid` |
| **Poller/Fallback → Laravel** | `https://yourdomain.com/webhook/aegis` | `webhook.aegis` |

These routes are defined in `routes/web.php` and handled by individual PHP scripts in `webhooks/`.

---

## Poller Architecture (NEW - 10-Second Polling Loop)

### How It Works

```
Cron (* * * * *) → main.py → poller.main()
                           ↓
                    30 × 10s loops (5 min max)
                           ↓
                    For each active session:
                      1. query_provider_balance() → routes to correct provider
                         ETH/ERC20 → Alchemy (eth_getBalance / eth_call balanceOf)
                         BSC/BEP20 → Ankr (eth_getBalance / eth_call balanceOf)
                         TRX/TRC20 → TronGrid (/v2/accounts/ / /triggerconstantcontract)
                         BTC/LTC/DOGE → BlockCypher (/addrs/?unspentOnly=true)
                      2. If balance ≥ expected:
                            get_transaction_hash() → finds tx_hash
                            poll_confirmations() → waits for required confirmations
                            POST /webhook/aegis → Laravel credits user
                      3. If expires_at < NOW: status='expired'
```

### Poller Configuration
- **Interval**: 10 seconds
- **Max iterations per cron**: 30 (5 minutes max - cPanel limit)
- **Session window**: 15 minutes from `created_at`
- **Lock mechanism**: File lock (`/tmp/crypto_poller.lock`) prevents concurrent instances
- **Graceful shutdown**: Handles SIGTERM/SIGINT, finishes current iteration

---

## Fallback Watcher Transaction Hash Discovery

The fallback watcher (runs every 5 min) uses provider-specific APIs to find transaction hashes when webhooks/poller miss a payment:

| Chain | Method | API |
|-------|--------|-----|
| ETH/ERC20 | Alchemy `alchemy_getAssetTransfers` | Enhanced API - filters by `toAddress`, category, amount |
| BSC/BEP20 | BscScan `tokentx`/`txlist` (primary) | `https://api.bscscan.com/api?module=account&action=tokentx` |
| BSC (fallback) | Ankr `eth_getLogs` | Transfer event topic + address topic filter |
| TRX/TRC20 | TronGrid v2 | `GET /v2/accounts/{address}/transactions[/trc20]` |
| BTC/LTC/DOGE | BlockCypher | `/addrs/{address}/full` (already implemented) |

### BSCSCAN_API_KEY (Optional but Recommended)
Add to `crypto_service/.env`:
```env
BSCSCAN_API_KEY=your_bscscan_api_key
```
Without this, BSC falls back to Ankr `eth_getLogs` which is less reliable for historical lookups.

---

## .env Configuration

### crypto_service/.env
```env
# Database
DB_HOST=127.0.0.1
DB_PORT=3306
DB_DATABASE=hyip
DB_USERNAME=root
DB_PASSWORD=

# Seed Phrase
AEGIS_SEED_PHRASE=your_twelve_word_seed_phrase_here

# Main Wallets
MAIN_WALLET_BTC=your_btc_address
MAIN_WALLET_ETH=your_eth_address
MAIN_WALLET_BSC=your_bsc_address
MAIN_WALLET_LTC=your_ltc_address
MAIN_WALLET_DOGE=your_doge_address
MAIN_WALLET_TRX=your_trx_address

# API Keys (4 Provider Architecture)
ALCHEMY_API_KEY=your_alchemy_key
BLOCKCYPHER_API_KEY=your_blockcypher_key
ANKR_API_KEY=your_ankr_key
TRONGRID_API_KEY=your_trongrid_key

# Optional: BscScan for BSC transaction hash lookup (fallback watcher)
BSCSCAN_API_KEY=your_bscscan_key

# App Key (Laravel APP_KEY for internal auth)
APP_KEY=base64:your_laravel_app_key

# Laravel Webhook Integration
AEGIS_WEBHOOK_URL=https://yourdomain.com/webhook/aegis
AEGIS_WEBHOOK_TIMEOUT=30
```

### hyip/.env (add to existing)
```env
# AegisPayment XPUB Keys (for address generation)
XPUB_BTC=xpub6...
XPUB_ETH=xpub6...
XPUB_LTC=xpub6...
XPUB_DOGE=dgub...
XPUB_BSC=xpub6...
XPUB_TRX=xpub6...

# Logo URL for payment pages
AEGIS_LOGO_URL=https://yourdomain.com/logo.png

# Aegis Crypto Service Webhook URL (for Python poller to notify Laravel)
AEGIS_WEBHOOK_URL=https://yourdomain.com/webhook/aegis
```

---

## Webhook URL Configuration

| Provider | Webhook URL | Route Name |
|----------|-------------|------------|
| Alchemy | `https://yourdomain.com/webhook/alchemy` | `webhook.alchemy` |
| BlockCypher | `https://yourdomain.com/webhook/blockcypher` | `webhook.blockcypher` |
| Ankr | `https://yourdomain.com/webhook/ankr` | `webhook.ankr` |
| TronGrid | `https://yourdomain.com/webhook/trongrid` | `webhook.trongrid` |
| **Poller/Fallback → Laravel** | `https://yourdomain.com/webhook/aegis` | `webhook.aegis` |

---

## Complete Payment Flow (All Paths)

```
1. User clicks "Pay" → HYIP creates crypto_payments record (status='pending')
       │
       ▼
2. HYIP shows payment address + QR code
       │
       ▼
3. User sends crypto to address
       │
       ▼
4. Blockchain confirms transaction
       │
       ├──► PATH 1: Webhook (Primary)
       │     Provider → Laravel webhook → webhook_processor.py
       │     │
       │     ├── Verify via RPC (rpc_client.py)
       │     ├── Check confirmations ≥ required
       │     └── If confirmed:
       │         ├── UPDATE status='confirmed', needs_sweep=1
       │         ├── Credit user via Txn::update()
       │         └── Mark webhook processed
       │
       ├──► PATH 2: Poller (10-sec loop)
       │     Cron → main.py → poller.py (30×10s)
       │     │
       │     ├── query_provider_balance() per session
       │     ├── If balance ≥ expected:
       │     │   ├── get_transaction_hash() → finds tx_hash
       │     │   ├── poll_confirmations() → waits for confirmations
       │     │   └── POST /webhook/aegis → Laravel credits user
       │     └── If expires_at < NOW: status='expired'
       │
       └──► PATH 3: Fallback Watcher (5-min cron)
             Cron → main.py → fallback_watcher.py
             │
             ├── query_provider_balance() for stale pending sessions
             ├── get_transaction_hash() via provider APIs
             ├── verify_transaction_rpc() for confirmations
             └── Same credit flow as above
       │
       ▼
5. sweeper.py (every 5 min via cron):
   ├─ Find confirmed deposits needing sweep
   ├─ Check gas balance
   ├─ Derive private key (BIP44)
   ├─ Execute sweep via tx_builder.py
   └─ UPDATE status='completed', swept=1
```

---

## Cron Job Setup (cPanel)

```bash
# Run crypto service every minute
* * * * * /usr/bin/python3 /home/user/crypto_service/main.py >> /home/user/crypto_service/logs/cron.log 2>&1
```

The `main.py` orchestrator runs all tasks:
1. Manual Actions (process admin queued actions)
2. Webhook Processor (process incoming webhooks)
3. Sweeper (consolidate confirmed funds)
4. Fallback Watcher (catch missed webhooks - 5 min window)
5. Address Generator (maintain address pools)
6. **Payment Poller (NEW - 10-sec loop, 15-min window)**

---

## Requirements.txt

```txt
# Core dependencies
python-dotenv>=1.2.2
PyMySQL>=1.1.1
requests>=2.32.3

# Blockchain libraries (compatible versions that work together)
web3>=7.16.0
eth-account>=0.13.7
tronpy>=0.6.2
bitcoinlib>=0.7.8

# BIP39/BIP44 key derivation
bip-utils>=2.12.1

# Optional: For better logging
colorlog>=6.10.1
```

### New Dependencies for Poller/Fallback Enhancements
**No new packages required** - all new functionality uses existing dependencies:
- `requests` - HTTP calls to all provider APIs (Alchemy, BscScan, TronGrid, BlockCypher, Ankr)
- `web3` - Ethereum/BSC transaction verification
- `tronpy` - TRON transaction verification
- `bitcoinlib` - UTXO transaction building/signing
- `bip-utils` - BIP44 key derivation
- `python-dotenv` - Environment variable loading

---

## Testing

### Test 1: RPC Verification
```bash
cd crypto_service
python rpc_client.py <tx_hash> <address> <chain>
# e.g., python rpc_client.py 0x123... 0xAbc... ETH
```

### Test 2: Key Derivation
```bash
python tx_builder.py BTC 0
python tx_builder.py ETH 0
python tx_builder.py TRX 0
```

### Test 3: End-to-End Deposit Flow (TESTNET RECOMMENDED)
1. Create deposit in HYIP
2. Send testnet crypto to provided address
3. Monitor webhook processing:
   ```bash
   python webhook_processor.py
   ```
4. Check database:
   ```sql
   SELECT id, address, status, confirmations, swept FROM crypto_payments ORDER BY id DESC LIMIT 1;
   ```
5. Run poller manually:
   ```bash
   python poller.py
   ```
6. Run sweeper:
   ```bash
   python sweeper.py
   ```
7. Verify final state:
   ```sql
   SELECT id, status, swept, sweep_tx_hash FROM crypto_payments WHERE id = X;
   ```

### Test 4: Fallback Watcher Transaction Lookup
```bash
# Test ETH transaction hash lookup
python -c "
from rpc_client import _get_transaction_hash_eth_alchemy
print(_get_transaction_hash_eth_alchemy('0xYourAddress', 0.01))
"

# Test TRX transaction hash lookup
python -c "
from rpc_client import _get_transaction_hash_trx_trongrid
print(_get_transaction_hash_trx_trongrid('TYourAddress', 10))
"
```

### Test 5: Manual Actions
```sql
-- Retry failed sweep
INSERT INTO manual_actions_log (action_type, chain, address_from, status) 
VALUES ('retry_sweep', 'ETH', '0xDepositAddress', 'pending');
```
```bash
python manual_actions.py
```

---

## Troubleshooting

### Deposits Not Confirming
- Check webhook events table: `SELECT * FROM webhook_events WHERE processed = 0;`
- Run fallback watcher manually: `python fallback_watcher.py`
- Run poller manually: `python poller.py`
- Verify RPC API keys in `.env`
- Check poller lock: `ls -la /tmp/crypto_poller.lock`

### Sweeps Failing
- Check sweeper logs: `tail -f logs/sweeper.log`
- Verify main wallet addresses in `.env`
- Check gas balances: `python -c "from rpc_client import query_provider_balance; print(query_provider_balance('ETH', '0xAddress'))"`

### Address Pool Running Low
- Check pool status: `SELECT address_family, COUNT(*) FROM address_pool WHERE used = 0 GROUP BY address_family;`
- Generate more: `python generator.py`

### Poller Not Running
- Check cron log: `tail -f logs/cron.log`
- Check poller log: `tail -f logs/poller.log`
- Verify lock file not stuck: `rm -f /tmp/crypto_poller.lock`

### Webhook Not Received
- Verify webhook URLs are accessible from internet
- Check provider dashboard for delivery failures
- Ensure SSL certificate is valid

---

## Security Checklist

- [ ] Seed phrase stored securely offline
- [ ] API keys in `.env` (not in code)
- [ ] `.env` excluded from git (`.gitignore`)
- [ ] Main wallet uses hardware wallet (recommended)
- [ ] Server firewall enabled
- [ ] SSL/TLS enabled for webhook endpoints
- [ ] Regular database backups
- [ ] Monitoring alerts configured
- [ ] Test on testnet before mainnet
- [ ] BSCSCAN_API_KEY added for reliable BSC transaction lookup

---

## Support

For issues, check:
1. Logs in `crypto_service/logs/`
2. Database tables for stuck records
3. Admin dashboard for manual recovery
4. Provider dashboards for API quotas/errors