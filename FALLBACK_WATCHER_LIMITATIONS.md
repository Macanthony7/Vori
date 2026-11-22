# Fallback Watcher Limitations and Architecture

## Overview
The fallback watcher is designed as a **secondary verification layer** that re-checks deposits that already have a `tx_hash` but may have stalled during confirmation tracking. It is NOT designed to discover new transactions.

## Current Behavior

### What Fallback Watcher DOES
✅ Scans deposits with status `IN ('pending', 'confirming')` (line 124)
✅ Re-verifies transactions that have a `tx_hash` via RPC (line 99)
✅ Updates confirmation counts for stuck deposits
✅ Finalizes deposits that reached required confirmations
✅ Catches webhook failures AFTER initial transaction detection

### What Fallback Watcher DOES NOT DO
❌ Discover new transactions without a `tx_hash`
❌ Scan blockchain addresses for incoming payments
❌ Act as primary transaction detection mechanism

## The Gap: Missing tx_hash

**Location**: `fallback_watcher.py` line 91-94

```python
if not tx_hash:
    log_info(logger, f"Deposit #{deposit_id}: No tx_hash yet, skipping RPC verification (webhook needed)")
    return False
```

**Why this exists**:
- RPC verification requires a transaction hash to query blockchain APIs
- Without tx_hash, there's nothing to verify via `verify_transaction_rpc()`
- Discovering transactions requires address scanning, not transaction verification

## Scenarios

### Scenario 1: Webhook Works (Normal Flow) ✅
1. User sends crypto to deposit address
2. Webhook receives notification with tx_hash
3. webhook_processor.py stores tx_hash in crypto_payments
4. If webhook stalls on confirmations, fallback picks it up
5. **Result**: Works perfectly

### Scenario 2: Webhook Fails Completely ❌
1. User sends crypto to deposit address
2. Webhook never fires (service down, network issue, etc.)
3. crypto_payments record has no tx_hash
4. Fallback watcher skips it (can't verify without tx_hash)
5. **Result**: Payment stuck in 'pending' forever

### Scenario 3: Webhook Delayed ⚠️
1. User sends crypto to deposit address
2. Webhook delayed by several minutes
3. Fallback runs before webhook arrives
4. Fallback skips (no tx_hash yet)
5. Webhook eventually arrives and processes normally
6. **Result**: Works, but with delay

## Why Address Scanning Is Not Implemented

Address scanning would require:

1. **Different API Endpoints**
   - BlockCypher: `/addrs/{address}/full` (includes all transactions)
   - Etherscan: `?module=account&action=txlist&address={address}`
   - BscScan: `?module=account&action=txlist&address={address}`
   - TronGrid: `/v1/accounts/{address}/transactions`

2. **Pagination and Rate Limits**
   - Must handle multiple transactions per address
   - API rate limits more restrictive for address queries
   - Requires tracking last scanned block/timestamp

3. **Transaction Filtering**
   - Must filter for incoming transactions only
   - Must match expected amount (with tolerance)
   - Must handle multiple deposits to same address
   - Must avoid duplicate processing

4. **Performance Concerns**
   - Scanning 1000s of addresses every 5 minutes
   - Each address query costs API credits
   - Significantly higher load than tx_hash verification

5. **Complexity**
   - Requires maintaining scan state per address
   - Must handle blockchain reorganizations
   - More error-prone than webhook + fallback

## Recommended Solutions

### Solution 1: Webhook Redundancy (Recommended)
**Implement multiple webhook providers**

```python
# In webhook_processor.py
WEBHOOK_PROVIDERS = [
    'primary_webhook_service',
    'backup_webhook_service_1',
    'backup_webhook_service_2'
]
```

**Pros**:
- Minimal code changes
- Leverages existing infrastructure
- Low API cost
- High reliability

**Cons**:
- Requires multiple webhook service subscriptions
- Must deduplicate webhook events

### Solution 2: Address Scanner (Comprehensive)
**Implement dedicated address scanning service**

Create new file: `address_scanner.py`

```python
def scan_address_for_transactions(address, chain, last_scanned_block):
    """
    Scan blockchain for transactions to a specific address.
    Returns list of new transactions since last_scanned_block.
    """
    # Implementation would query blockchain APIs
    # Filter for incoming transactions
    # Return tx_hash, amount, confirmations
    pass

def scan_pending_addresses():
    """
    Scan all pending deposits without tx_hash.
    Run less frequently than fallback (e.g., every 15 minutes).
    """
    deposits = execute_query(
        """SELECT * FROM crypto_payments 
           WHERE status = 'pending' 
           AND tx_hash IS NULL 
           AND created_at > DATE_SUB(NOW(), INTERVAL 24 HOUR)
           ORDER BY created_at ASC 
           LIMIT 100"""
    )
    
    for deposit in deposits:
        transactions = scan_address_for_transactions(
            deposit['address'], 
            deposit['address_family'],
            deposit.get('last_scanned_block', 0)
        )
        
        for tx in transactions:
            # Process discovered transaction
            # Update crypto_payments with tx_hash
            # Trigger verification flow
            pass
```

**Pros**:
- Complete solution for webhook failures
- Can discover any payment
- Independent of webhook services

**Cons**:
- Significant development effort
- Higher API costs
- More complex error handling
- Requires careful rate limit management

### Solution 3: Manual Admin Intervention (Current Workaround)
**Admin manually adds tx_hash for stuck deposits**

1. User reports payment not detected
2. Admin checks blockchain explorer for user's address
3. Admin finds transaction hash
4. Admin updates crypto_payments:
   ```sql
   UPDATE crypto_payments 
   SET tx_hash = '0x...' 
   WHERE id = 123;
   ```
5. Fallback watcher picks it up on next run

**Pros**:
- No code changes needed
- Works immediately
- Handles edge cases

**Cons**:
- Requires manual intervention
- Not scalable
- Poor user experience

### Solution 4: Hybrid Approach (Balanced)
**Webhook primary + Limited address scanning for old deposits**

```python
def scan_old_pending_deposits():
    """
    Only scan deposits older than 30 minutes without tx_hash.
    Assumes webhook should have arrived by then.
    """
    cutoff_time = datetime.now() - timedelta(minutes=30)
    
    deposits = execute_query(
        """SELECT * FROM crypto_payments 
           WHERE status = 'pending' 
           AND tx_hash IS NULL 
           AND created_at < %s
           AND created_at > DATE_SUB(NOW(), INTERVAL 24 HOUR)
           LIMIT 50""",
        (cutoff_time,)
    )
    
    # Scan these addresses only
    # Lower frequency, lower volume
```

**Pros**:
- Catches webhook failures without full scanning
- Lower API cost than full scanning
- Reasonable user experience

**Cons**:
- Still requires address scanning implementation
- 30-minute delay for webhook failures

## Current Mitigation Strategies

### 1. Webhook Monitoring
Monitor webhook delivery success rate:
```sql
SELECT 
    DATE(created_at) as date,
    COUNT(*) as total_deposits,
    SUM(CASE WHEN tx_hash IS NOT NULL THEN 1 ELSE 0 END) as with_txhash,
    SUM(CASE WHEN tx_hash IS NULL AND status = 'pending' THEN 1 ELSE 0 END) as stuck
FROM crypto_payments
WHERE created_at > DATE_SUB(NOW(), INTERVAL 7 DAY)
GROUP BY DATE(created_at);
```

### 2. Alert on Stuck Deposits
```sql
SELECT id, address, coin, amount, created_at
FROM crypto_payments
WHERE status = 'pending'
AND tx_hash IS NULL
AND created_at < DATE_SUB(NOW(), INTERVAL 1 HOUR);
```

### 3. User Communication
- Display clear instructions: "Payment detected within 10 minutes"
- Provide support contact for stuck payments
- Show blockchain explorer link for user to verify

### 4. Expiration Handling
- Deposits expire after 24 hours (check_status.php line 31)
- User can generate new address if payment stuck
- Prevents indefinite pending state

## Deployment Recommendations

### Immediate (Current State)
1. ✅ Deploy current fallback watcher (handles webhook stalls)
2. ✅ Monitor webhook success rate
3. ✅ Set up alerts for deposits without tx_hash > 1 hour
4. ✅ Document manual intervention process for admins

### Short Term (1-2 weeks)
1. Implement webhook redundancy (Solution 1)
2. Add webhook health monitoring
3. Create admin tool to manually add tx_hash

### Medium Term (1-2 months)
1. Implement limited address scanner (Solution 4)
2. Scan only deposits > 30 minutes old without tx_hash
3. Run scanner every 15 minutes (less frequent than fallback)

### Long Term (3+ months)
1. Full address scanning implementation (Solution 2)
2. Blockchain reorganization handling
3. Advanced transaction matching algorithms

## Risk Assessment

### Current Risk Level: **LOW-MEDIUM**

**Why LOW**:
- Webhooks are generally reliable (95%+ success rate)
- Fallback catches webhook confirmation stalls
- Manual intervention available
- Deposits expire after 24 hours

**Why MEDIUM**:
- Complete webhook failure leaves deposits stuck
- Requires manual admin intervention
- Poor UX for affected users
- No automated recovery

### Acceptable Risk If:
- Webhook service has >99% uptime SLA
- Admin team available 24/7 for manual intervention
- User volume is low-medium (<1000 deposits/day)
- Support team can handle stuck payment tickets

### Unacceptable Risk If:
- High user volume (>1000 deposits/day)
- No 24/7 admin coverage
- Webhook service unreliable
- Regulatory requirements for automated processing

## Conclusion

The fallback watcher's inability to discover transactions without tx_hash is a **known architectural limitation**, not a bug. It's designed to be a secondary verification layer, not a primary detection mechanism.

**Current state**: Production-ready for webhook-primary architecture with manual fallback

**Recommended next step**: Implement webhook redundancy (Solution 1) for improved reliability

**Future enhancement**: Add limited address scanning (Solution 4) for complete automation

## Related Files
- `fallback_watcher.py` - Current implementation
- `webhook_processor.py` - Primary transaction detection
- `check_status.php` - Frontend status polling
- `crypto_payments` table - Deposit tracking

## Monitoring Queries

### Check for stuck deposits
```sql
SELECT COUNT(*) as stuck_count
FROM crypto_payments
WHERE status = 'pending'
AND tx_hash IS NULL
AND created_at < DATE_SUB(NOW(), INTERVAL 1 HOUR)
AND created_at > DATE_SUB(NOW(), INTERVAL 24 HOUR);
```

### Webhook success rate (last 24h)
```sql
SELECT 
    COUNT(*) as total,
    SUM(CASE WHEN tx_hash IS NOT NULL THEN 1 ELSE 0 END) as with_txhash,
    ROUND(SUM(CASE WHEN tx_hash IS NOT NULL THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 2) as success_rate
FROM crypto_payments
WHERE created_at > DATE_SUB(NOW(), INTERVAL 24 HOUR);
```

### Average time to tx_hash
```sql
SELECT 
    address_family as chain,
    AVG(TIMESTAMPDIFF(SECOND, created_at, updated_at)) as avg_seconds_to_txhash
FROM crypto_payments
WHERE tx_hash IS NOT NULL
AND created_at > DATE_SUB(NOW(), INTERVAL 7 DAY)
GROUP BY address_family;
```
