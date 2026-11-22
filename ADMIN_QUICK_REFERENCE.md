# AEGIS CRYPTO SERVICE - ADMIN QUICK REFERENCE (4-Provider Edition)

## SYSTEM STATUS CHECK

### Check Service Health
```bash
cd C:\xampp8.2\htdocs\aegis\crypto_service

# Check recent logs
type logs\sweeper.log | findstr /C:"ERROR" /C:"WARNING"
type logs\manual_actions.log | findstr /C:"ERROR" /C:"WARNING"
type logs\poller.log | findstr /C:"ERROR" /C:"WARNING"
```

### Database Health Check
```sql
-- Pending deposits (waiting for payment, 15-min window)
SELECT COUNT(*) as pending FROM crypto_payments 
WHERE status = 'pending' AND expires_at > NOW();

-- Confirming deposits (payment detected, accumulating confirmations)
SELECT COUNT(*) as confirming FROM crypto_payments WHERE status = 'confirming';

-- Confirmed deposits (user credited, waiting for sweep)
SELECT COUNT(*) as confirmed FROM crypto_payments 
WHERE status = 'confirmed' AND swept = 0 AND needs_sweep = 1;

-- Completed deposits (user credited + swept)
SELECT COUNT(*) as completed FROM crypto_payments WHERE status = 'completed';

-- Expired deposits (15-min window passed without payment)
SELECT COUNT(*) as expired FROM crypto_payments WHERE status = 'expired';

-- Failed manual actions
SELECT * FROM manual_actions_log WHERE status = 'failed' ORDER BY created_at DESC LIMIT 10;

-- Address pool status
SELECT address_family, COUNT(*) as available 
FROM address_pool WHERE used = 0 GROUP BY address_family;
```

---

## COMMON ISSUES & FIXES

### Issue: Deposit Stuck at "Confirming"
**Cause**: Not enough blockchain confirmations yet, or poller missed the payment
**Action**: Wait for more confirmations, or check if poller is running

```sql
-- Check confirmation count
SELECT id, address, confirmations, expected_confirmations, tx_hash 
FROM crypto_payments WHERE status = 'confirming';
```

**Manual Check** (if poller missed):
```bash
# Test balance check directly
python -c "
from rpc_client import query_provider_balance
print(query_provider_balance('BTC', 'address_here'))
"
```

---

### Issue: Deposit Confirmed but Not Swept
**Cause**: Insufficient gas, sweep failed, or sweeper not running
**Action**: Check sweeper logs and gas balance

```sql
-- Find deposits needing sweep
SELECT id, address, address_family, paid_amount, confirmed_at 
FROM crypto_payments 
WHERE status = 'confirmed' AND swept = 0 AND needs_sweep = 1;
```

**Check Gas Balance**:
```bash
python -c "from rpc_client import check_address_balance; print(check_address_balance('0xAddress...', 'ETH'))"
```

**Manual Sweep**:
```sql
-- Queue manual send action
INSERT INTO manual_actions_log (action_type, chain, address_from, address_to, amount, status)
VALUES ('retry_sweep', 'ETH', '0xDepositAddress', NULL, 0, 'pending');
```

```bash
python manual_actions.py
```

---

### Issue: "Provider URL not configured" Warning
**Cause**: Provider API keys not set in .env
**Action**: Add provider keys to .env file

```env
# 4-Provider Architecture Keys
ALCHEMY_API_KEY=your_alchemy_key
BLOCKCYPHER_API_KEY=your_blockcypher_key
ANKR_API_KEY=your_ankr_key
TRONGRID_API_KEY=your_trongrid_key
BSCSCAN_API_KEY=your_bscscan_key
```

**Test RPC**:
```bash
python rpc_client.py <tx_hash> <address> <chain>
```

---

### Issue: Sweep Fails with "Insufficient Gas"
**Cause**: Deposit address has no ETH/BNB/TRX for gas fees
**Action**: Fund gas manually using external wallet

**For ETH/BSC**:
1. Open MetaMask
2. Send 0.001 ETH or 0.002 BNB to deposit address
3. Retry sweep:
```sql
UPDATE crypto_payments SET needs_sweep = 1 WHERE id = X;
```
```bash
python sweeper.py
```

**For TRX**:
1. Open TronLink
2. Send 10 TRX to deposit address
3. Retry sweep (same as above)

---

### Issue: Address Pool Running Low
**Cause**: Many deposits created, pool depleted
**Action**: Generate more addresses

```sql
-- Check pool status
SELECT address_family, COUNT(*) as available 
FROM address_pool WHERE used = 0 GROUP BY address_family;
```

**Generate More**:
```bash
python generator.py
```

---

## MANUAL INTERVENTION

### Retry Failed Sweep
```sql
-- Find deposit ID
SELECT id, address, address_family FROM crypto_payments 
WHERE swept = 0 AND status = 'confirmed';

-- Queue retry
INSERT INTO manual_actions_log (action_type, chain, address_from, address_to, amount, status)
VALUES ('retry_sweep', 'ETH', '0xDepositAddress', NULL, 0, 'pending');
```

```bash
python manual_actions.py
```

### Manual Send Transaction
```sql
-- Send funds from deposit address to main wallet
INSERT INTO manual_actions_log (action_type, chain, address_from, address_to, amount, status)
VALUES ('send', 'BTC', '1DepositAddress', '1MainWallet', 0, 'pending');
```

```bash
python manual_actions.py
```

### Check Manual Action Status
```sql
SELECT id, action_type, chain, address_from, status, tx_hash, error_message, created_at
FROM manual_actions_log
ORDER BY id DESC LIMIT 20;
```

---

## MONITORING QUERIES

### Today's Deposits
```sql
SELECT 
    address_family as chain,
    COUNT(*) as count,
    SUM(paid_amount) as total_amount,
    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed
FROM crypto_payments
WHERE DATE(created_at) = CURDATE()
GROUP BY address_family;
```

### Deposits by Status (Last 7 Days)
```sql
SELECT 
    status,
    COUNT(*) as count,
    SUM(paid_amount) as total_amount
FROM crypto_payments
WHERE created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
GROUP BY status;
```

### Recent Sweeps
```sql
SELECT 
    id,
    address_family,
    address,
    paid_amount,
    sweep_tx_hash,
    swept_at
FROM crypto_payments
WHERE swept = 1
ORDER BY swept_at DESC
LIMIT 20;
```

### User Balance Changes (Last 24h)
```sql
SELECT 
    t.id,
    t.user_id,
    u.email,
    t.amount,
    t.status,
    t.created_at
FROM transactions t
JOIN users u ON t.user_id = u.id
WHERE t.type IN ('deposit', 'manual_deposit')
AND t.created_at >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
ORDER BY t.created_at DESC;
```

---

## EMERGENCY PROCEDURES

### Stop All Processing
```bash
# Stop cron job in cPanel Task Scheduler
# Or rename main.py temporarily
ren main.py main.py.disabled
```

### Emergency Fund Recovery
If sweeper is broken and funds need immediate recovery:

```sql
-- Get deposit details
SELECT id, address, address_family, address_index, paid_amount
FROM crypto_payments
WHERE swept = 0 AND status = 'confirmed';

-- Queue manual send for each
INSERT INTO manual_actions_log (action_type, chain, address_from, address_to, amount, status)
VALUES ('send', 'ETH', '0xDepositAddress', '0xMainWallet', 0, 'pending');
```

```bash
python manual_actions.py
```

### Verify Transaction on Blockchain
```bash
# Ethereum
https://etherscan.io/tx/<tx_hash>

# BSC
https://bscscan.com/tx/<tx_hash>

# Bitcoin
https://blockchair.com/bitcoin/transaction/<tx_hash>

# TRON
https://tronscan.org/#/transaction/<tx_hash>
```

---

## MAINTENANCE TASKS

### Daily
- [ ] Check error logs
- [ ] Verify sweep completion rate
- [ ] Check address pool levels
- [ ] Review failed manual actions

### Weekly
- [ ] Verify main wallet balances
- [ ] Check provider credit usage (Alchemy, Ankr, BlockCypher, TronGrid)
- [ ] Review deposit/sweep statistics
- [ ] Test RPC connectivity

### Monthly
- [ ] Rotate provider endpoints (if needed)
- [ ] Backup database
- [ ] Review security logs
- [ ] Update dependencies: `pip install -r requirements.txt --upgrade`

---

## QUICK COMMANDS

```bash
# Run all processors manually
cd C:\xampp8.2\htdocs\aegis\crypto_service
python main.py

# Run individual processors
python poller.py          # Replaces webhook_processor + fallback_watcher
python sweeper.py
python manual_actions.py
python generator.py

# Test RPC
python rpc_client.py <tx_hash> <address> <chain>

# Test key derivation
python tx_builder.py <chain> <index>

# Check logs
type logs\*.log | findstr /C:"ERROR"
```

---

## PROVIDER DASHBOARDS

Monitor credit usage at:
- **Alchemy**: https://dashboard.alchemy.com/
- **Ankr**: https://www.ankr.com/rpc/
- **BlockCypher**: https://www.blockcypher.com/
- **TronGrid**: https://www.trongrid.io/
- **BscScan** (optional): https://bscscan.com/

### Credit-Saving Tips
- Endpoints created with "Query raw blockchain data" only
- No Streams/Webhooks enabled
- Polling only runs 15 min per payment session
- Expected: ~18,000+ payment sessions/month on free tiers

---

## ROLLBACK PLAN

If issues arise post-migration:

1. **Re-enable webhook_processor.py** from git history
2. **Restore legacy API keys** to .env:
   ```env
   ALCHEMY_API_KEY=your_key
   ANKR_API_KEY=your_key
   BLOCKCYPHER_API_KEY=your_key
   TRONGRID_API_KEY=your_key
   ```
3. **Update cron** to use old main.py (with webhook_processor + fallback_watcher)
4. **No database changes required** - schema is backward compatible

---

## KEY FILES

- Config: `crypto_service/.env`
- Chain Config: `crypto_service/chains.json`
- Main Script: `crypto_service/main.py`
- Setup Guide: `crypto_service/setupguide.md`
- This Guide: `crypto_service/ADMIN_QUICK_REFERENCE.md`

---

## QUICK CONTACT

For issues, check:
1. Logs in `crypto_service/logs/`
2. Database tables for stuck records
3. Admin dashboard for manual recovery
4. Provider dashboards for credit/rate limits

**Remember**: Always test on TESTNET before production changes!