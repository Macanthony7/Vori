# Crypto Service - Multi-Chain Payment System (4-Provider Architecture)

Production-ready crypto payment system supporting BTC, ETH, BSC, LTC, DOGE, and TRON via **4 specialized providers**: Alchemy (ETH/ERC20), Ankr (BSC/BEP20), BlockCypher (BTC/LTC/DOGE), TronGrid (TRX/TRC20).

## Architecture

```
BLOCKCHAIN → PROVIDER RPC → crypto_service (poller.py) → DATABASE → Laravel (HYIP Panel)
```

### Key Change: Webhook-Free Polling Architecture
- **No webhooks required** - eliminates dependency on blockchain provider webhook reliability
- **Session-based polling**: 15-minute active window, 10-second intervals
- **cPanel cron compatible**: 1-minute cron trigger, 5-minute internal loop
- **4 specialized providers**: Each chain uses the best provider for its ecosystem

## Components

- **generator.py** - Address pool management (auto-refills when below 200, max 2000)
- **poller.py** - **NEW**: Session-based 10s polling engine (replaces webhook_processor + fallback_watcher)
- **rpc_client.py** - Unified 4-provider RPC client (verification, balance, confirmations)
- **tx_builder.py** - Unified transaction builder (sweep native + tokens, all chains)
- **sweeper.py** - Moves confirmed funds to main wallet
- **manual_actions.py** - Admin operations (retry sweep, manual send, fund gas)
- **main.py** - Single entry point for cron execution

## Provider Mapping

| Chain | Provider | Purpose |
|-------|----------|---------|
| BTC, LTC, DOGE | BlockCypher | UTXO transaction verification, balance, UTXO fetching, broadcast |
| ETH, ERC20 | Alchemy | ETH native + ERC20 token verification, balance, gas, broadcast |
| BSC, BEP20 | Ankr (primary) / BscScan (fallback) | BNB native + BEP20 token verification, balance, gas, broadcast |
| TRX, TRC20 | TronGrid | TRX native + TRC20 token verification, balance, energy, broadcast |

## Installation

### 1. Install Dependencies

```bash
cd /home/user/crypto_service
pip3 install -r requirements.txt
```

### 2. Configure Provider Endpoints

Add to `.env`:

```env
# Database
DB_HOST=127.0.0.1
DB_PORT=3306
DB_DATABASE=hyip
DB_USERNAME=root
DB_PASSWORD=

# Seed Phrase (KEEP SECURE!)
AEGIS_SEED_PHRASE="your twelve word seed phrase here"

# Main Wallets
MAIN_WALLET_BTC=your_btc_address
MAIN_WALLET_ETH=your_eth_address
MAIN_WALLET_BSC=your_bsc_address
MAIN_WALLET_LTC=your_ltc_address
MAIN_WALLET_DOGE=your_doge_address
MAIN_WALLET_TRX=your_trx_address

# 4-Provider Architecture Keys
ALCHEMY_API_KEY=your_alchemy_key
BLOCKCYPHER_API_KEY=your_blockcypher_key
ANKR_API_KEY=your_ankr_key
TRONGRID_API_KEY=your_trongrid_key
BSCSCAN_API_KEY=your_bscscan_key  # Optional but recommended for BSC tx lookup

# App Key (Laravel APP_KEY for internal auth)
APP_KEY=base64:your_laravel_app_key

# Laravel Webhook Integration
AEGIS_WEBHOOK_URL=https://yourdomain.com/webhook/aegis
AEGIS_WEBHOOK_TIMEOUT=30
```

### Provider Setup Notes

**Alchemy (ETH/ERC20)**: 
- Create app at https://dashboard.alchemy.com/
- **Enable Enhanced APIs** (required for `alchemy_getAssetTransfers`)

**Ankr (BSC/BEP20)**:
- Get API key at https://www.ankr.com/rpc/
- Endpoint: `https://rpc.ankr.com/bsc/{ANKR_API_KEY}`

**BlockCypher (BTC/LTC/DOGE)**:
- Get token at https://www.blockcypher.com/
- Used for UTXO chains: BTC, LTC, DOGE

**TronGrid (TRX/TRC20)**:
- Create project at https://www.trongrid.io/
- **Enable Events → Contract Events → Transfer** for webhook support

**BscScan (Optional - BSC tx lookup)**:
- Get API key at https://bscscan.com/apis
- Used by fallback watcher for reliable BSC transaction hash lookup

### 3. Run Migrations

```bash
cd /home/user/hyip
php artisan migrate
```

### 3. Setup Cron Job (cPanel)

```bash
# Run crypto service every minute
* * * * * /usr/bin/python3 /home/user/crypto_service/main.py >> /home/user/crypto_service/cron.log 2>&1
```

**Note**: `poller.py` runs internal 30 × 10s loops = 5 minutes max per cron execution.

## Database Tables

- **address_pool** - Pre-generated addresses (unused/assigned/used)
- **crypto_payments** - User deposits with sweep status
- **processed_transactions** - Deduplication tracking
- **manual_actions_log** - Admin action history
- ~~webhook_events~~ - **Deprecated** (can be dropped after migration)

## Address Pool Logic

### Thresholds
- **MIN_POOL_SIZE**: 200 addresses
- **MAX_POOL_SIZE**: 2000 addresses
- **REFILL_BATCH**: 100 addresses per batch

### Flow
1. Cron runs `generator.py` every minute
2. Checks unused count per chain
3. If < 200: Refills to 2000
4. If >= 2000: Skips generation

## Polling Flow (NEW - Replaces Webhooks)

```
1. User clicks "Pay" → HYIP creates crypto_payments record (status='pending', expires_at=+15min)
       │
       ▼
2. cPanel cron (* * * * *) triggers main.py
       │
       ▼
3. poller.py starts internal loop (30 iterations × 10s = 5 min)
       │
       ├─► GET active sessions: status IN ('pending','confirming') AND created_at >= NOW()-15min
       │
       ├─► FOR each session: query_provider_balance() → routes to correct provider
       │     ETH/ERC20 → Alchemy (eth_getBalance / eth_call balanceOf)
       │     BSC/BEP20 → Ankr (eth_getBalance / eth_call balanceOf)
       │     TRX/TRC20 → TronGrid (/v2/accounts/ / /wallet/triggerconstantcontract)
       │     BTC/LTC/DOGE → BlockCypher (/v1/{coin}/main/addrs/{addr}?unspentOnly=true)
       │
       ├─► If balance >= expected:
       │     ├─ get_transaction_hash() → finds tx_hash
       │     ├─ poll_confirmations() → waits for required confirmations
       │     ├─ UPDATE status='confirmed', needs_sweep=1
       │     └─ Credit user balance in HYIP transactions table
       │
       └─► If expires_at passed: UPDATE status='expired'
```

## Sweep Flow

1. `sweeper.py` finds confirmed deposits with `needs_sweep = 1`
2. Checks gas balance (for EVM/TRON chains)
3. Derives private key from seed + index
4. **Native sweep**: Build, sign, broadcast via provider
5. **Token sweep**: Verify contract whitelisted → balanceOf → transfer → broadcast
6. Updates `swept = 1`, `sweep_tx_hash`, `status = 'completed'`

## Admin Dashboard

Access at: `/admin/crypto`

### Features
- Deposits dashboard (filter by status/chain)
- Transaction history
- Address pool status
- Stuck funds panel
- Manual actions (fund gas, retry sweep, manual send)

## Security

- Seed phrase stored ONLY in crypto service
- No private keys in Laravel/database
- All signing happens server-side
- Token contract whitelist prevents spoofing
- Admin actions logged

## Monitoring

Check logs:
```bash
tail -f /home/user/crypto_service/crypto_service.log
tail -f /home/user/crypto_service/cron.log
```

## Testing

### Test Address Generation
```bash
python3 generator.py
```

### Test Poller (single cycle)
```bash
python3 -c "from poller import run_poll_cycle; run_poll_cycle()"
```

### Test Full Cycle
```bash
python3 main.py
```

### Test Sweep
```bash
python3 sweeper.py
```

## Troubleshooting

### Pool not refilling
- Check cron is running
- Check database connection
- Check seed phrase in .env

### Deposits not confirming
- Check provider endpoints are active
- Verify 15-minute window hasn't expired
- Check `crypto_payments` table for pending records

### Sweeps failing
- Check main wallet addresses configured
- Check gas balances (ETH/BNB/TRX)
- Check manual_actions_log for errors

### Poller timeout
- cPanel max execution: 5 minutes
- Poller runs max 30 × 10s = 5 min
- If hitting limit, reduce MAX_LOOPS_PER_CRON

## Chain-Specific Notes

### BTC/LTC/DOGE (UTXO via BlockCypher)
- BlockCypher REST API
- Endpoints: `/v1/{coin}/main/addrs/{addr}`, `/v1/{coin}/main/txs/{hash}`, `/v1/{coin}/main/addrs/{addr}?unspentOnly=true`, `/v1/{coin}/main/txs/push`
- No gas needed
- UTXO-based sweeping

### ETH/BSC (EVM JSON-RPC)
- Alchemy/Ankr JSON-RPC
- Methods: `eth_getBalance`, `eth_getTransactionReceipt`, `eth_call`, `eth_sendRawTransaction`
- Requires gas (ETH/BNB)
- ERC20/BEP20 token support via `eth_call` to `balanceOf`/`transfer`

### TRON (TRON JSON-RPC)
- TronGrid JSON-RPC
- Methods: `wallet/getaccount`, `wallet/gettransactionbyid`, `wallet/triggerconstantcontract`, `wallet/broadcasttransaction`
- Requires TRX for energy/bandwidth
- TRC20 token support via `triggerconstantcontract`

## Production Checklist

- [ ] Alchemy endpoint created (Enhanced APIs enabled)
- [ ] Ankr BSC endpoint created
- [ ] BlockCypher token obtained
- [ ] TronGrid project created (TRC20 events enabled)
- [ ] BscScan API key added (recommended)
- [ ] QuickNode URLs added to `.env` - **REPLACED** with 4 provider keys
- [ ] Migrations run successfully
- [ ] Seed phrase configured securely
- [ ] Main wallets configured
- [ ] Cron job active (every minute)
- [ ] Address pool pre-filled (run `generator.py` once)
- [ ] Logs monitored
- [ ] Legacy API keys kept for rollback reference

## Migration from Webhook Architecture

If migrating from old webhook-based system:

1. **Deploy new code** alongside old (different directory)
2. **Add provider API keys** to new .env
3. **Run shadow mode**: Enable poller.py but keep webhook_processor running
4. **Compare**: Verify poller detects same payments as webhooks
5. **Cutover**: Update cron to new main.py, disable webhook endpoint
6. **Cleanup**: Remove old API keys, drop webhook_events table

## Rollback Plan

If issues arise post-migration:
1. Re-enable `webhook_processor.py` from git history
2. Restore legacy API keys to `.env`
3. Update cron to use old main.py
4. No database changes required (schema compatible)

## Support

For issues, check:
1. Logs in `crypto_service/logs/`
2. Database tables for stuck records
3. Admin dashboard for manual recovery
4. Provider dashboards for credit/rate limits

**All 5 blocking issues are resolved and production-ready with 4-Provider Architecture! 🚀**