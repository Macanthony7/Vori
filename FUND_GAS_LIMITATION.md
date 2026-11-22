# Manual Actions: fund_gas Limitation

## Status: NOT IMPLEMENTED

The `fund_gas` manual action is **intentionally not implemented** due to security concerns around main wallet key management.

## Current Behavior

### What Happens
1. Admin queues `fund_gas` action via admin UI
2. Action is inserted into `manual_actions_log` with status='pending'
3. `manual_actions.py` worker picks up the action
4. Worker validates parameters (address, amount, chain)
5. Worker checks main wallet balance
6. **Worker always fails at line 67-68** with error:
   ```
   Main wallet key management not implemented - use external wallet
   ```
7. Action marked as 'failed' in database

### Why It Fails
The main wallet private key is **not accessible** to the crypto_service workers for security reasons:

**Security Principle**: Main wallet (hot wallet) should NOT use the same seed phrase as deposit addresses.

**Reasons**:
1. **Separation of Concerns**: Deposit addresses are ephemeral, main wallet is permanent
2. **Risk Isolation**: If deposit seed is compromised, main wallet remains safe
3. **Key Management**: Main wallet should use hardware wallet/HSM, not software key
4. **Audit Trail**: Main wallet transactions should require explicit authorization

## Manual Workaround (Current Solution)

When a deposit address needs gas funding, admin must manually send gas using external wallet.

### Step-by-Step Process

#### 1. Identify Deposits Needing Gas
```sql
SELECT 
    id,
    address,
    address_family as chain,
    coin,
    amount,
    created_at
FROM crypto_payments
WHERE status = 'confirmed'
AND swept = 0
AND needs_sweep = 1
AND address_family IN ('ETH', 'BSC', 'TRX')
ORDER BY created_at ASC;
```

#### 2. Check Gas Balance
For each address, check if it has sufficient gas:

**Ethereum (ETH)**:
- Minimum: 0.001 ETH (~$3 at $3000/ETH)
- Check: https://etherscan.io/address/{address}

**BSC (BNB)**:
- Minimum: 0.002 BNB (~$1 at $500/BNB)
- Check: https://bscscan.com/address/{address}

**TRON (TRX)**:
- Minimum: 10 TRX (~$2 at $0.20/TRX)
- Check: https://tronscan.org/#/address/{address}

#### 3. Send Gas from Main Wallet
Using your external wallet (MetaMask, Trust Wallet, TronLink, etc.):

**For ETH/BSC**:
```
From: Main Wallet Address
To: Deposit Address (from step 1)
Amount: 0.001 ETH (or 0.002 BNB)
Gas: Standard
```

**For TRX**:
```
From: Main Wallet Address
To: Deposit Address (from step 1)
Amount: 10 TRX
```

#### 4. Wait for Confirmation
- ETH: ~12 confirmations (~3 minutes)
- BSC: ~15 confirmations (~45 seconds)
- TRX: ~19 confirmations (~1 minute)

#### 5. Retry Sweep
Once gas is funded, retry the sweep:

**Option A: Via Admin UI**
- Navigate to manual actions
- Create "retry_sweep" action for the address

**Option B: Via Database**
```sql
UPDATE crypto_payments 
SET needs_sweep = 1, swept = 0 
WHERE address = '{deposit_address}';
```

**Option C: Via Manual Action**
```sql
INSERT INTO manual_actions_log 
(action_type, chain, address_from, status, created_at) 
VALUES ('retry_sweep', '{chain}', '{deposit_address}', 'pending', NOW());
```

#### 6. Verify Sweep Success
Check that sweep completed:
```sql
SELECT 
    id,
    address,
    status,
    swept,
    sweep_tx_hash,
    swept_at
FROM crypto_payments
WHERE address = '{deposit_address}';
```

Expected result:
- `status` = 'completed'
- `swept` = 1
- `sweep_tx_hash` = transaction hash
- `swept_at` = timestamp

## Automated Monitoring

### Alert on Low Gas Deposits
Create a monitoring query to identify deposits that need gas:

```sql
SELECT 
    COUNT(*) as deposits_needing_gas,
    address_family as chain,
    GROUP_CONCAT(address SEPARATOR ', ') as addresses
FROM crypto_payments
WHERE status = 'confirmed'
AND swept = 0
AND needs_sweep = 1
AND address_family IN ('ETH', 'BSC', 'TRX')
AND confirmed_at < DATE_SUB(NOW(), INTERVAL 1 HOUR)
GROUP BY address_family;
```

### Daily Gas Funding Report
```sql
SELECT 
    DATE(confirmed_at) as date,
    address_family as chain,
    COUNT(*) as total_confirmed,
    SUM(CASE WHEN swept = 1 THEN 1 ELSE 0 END) as swept,
    SUM(CASE WHEN swept = 0 AND needs_sweep = 1 THEN 1 ELSE 0 END) as needs_gas
FROM crypto_payments
WHERE confirmed_at > DATE_SUB(NOW(), INTERVAL 7 DAY)
AND address_family IN ('ETH', 'BSC', 'TRX')
GROUP BY DATE(confirmed_at), address_family
ORDER BY date DESC, chain;
```

## Future Implementation Options

### Option 1: Encrypted Main Wallet Key (Medium Security)
Store main wallet private key encrypted in database:

**Pros**:
- Automated gas funding
- No manual intervention needed

**Cons**:
- Key stored on server (security risk)
- If server compromised, main wallet at risk
- Not recommended for high-value wallets

**Implementation**:
```python
# In config.py
MAIN_WALLET_PRIVATE_KEY_ENCRYPTED = get_env('MAIN_WALLET_PRIVATE_KEY_ENCRYPTED')
ENCRYPTION_KEY = get_env('ENCRYPTION_KEY')

def get_main_wallet_private_key(chain):
    from cryptography.fernet import Fernet
    cipher = Fernet(ENCRYPTION_KEY)
    encrypted_key = MAIN_WALLET_PRIVATE_KEY_ENCRYPTED
    return cipher.decrypt(encrypted_key.encode()).decode()
```

### Option 2: Hardware Wallet / HSM (High Security)
Use hardware wallet or HSM for main wallet:

**Pros**:
- Private key never leaves secure device
- High security
- Industry best practice

**Cons**:
- Requires hardware wallet integration
- More complex setup
- May require manual approval per transaction

**Implementation**:
- Integrate with Ledger/Trezor API
- Or use AWS KMS / Azure Key Vault
- Or use dedicated HSM device

### Option 3: Multi-Signature Wallet (Highest Security)
Use multi-sig wallet requiring multiple approvals:

**Pros**:
- No single point of failure
- Requires multiple admins to approve
- Audit trail built-in

**Cons**:
- Most complex implementation
- Slower (requires multiple signatures)
- Higher gas costs

**Implementation**:
- Use Gnosis Safe or similar
- Require 2-of-3 or 3-of-5 signatures
- Integrate with multi-sig API

### Option 4: Separate Gas Funding Service (Recommended)
Create dedicated service for gas funding:

**Pros**:
- Isolated from main crypto_service
- Can use different security model
- Rate limiting and monitoring built-in

**Cons**:
- Additional service to maintain
- Still needs key management solution

**Implementation**:
```python
# gas_funder.py (separate service)
def fund_gas_if_needed(address, chain):
    balance = check_gas_balance(address, chain)
    if balance < MIN_GAS_THRESHOLD[chain]:
        # Use encrypted key or HSM
        send_gas(address, chain, GAS_AMOUNT[chain])
```

## Recommendation

**For Production**: Use **Option 4 (Separate Gas Funding Service)** with **Option 2 (Hardware Wallet/HSM)**

**Rationale**:
1. Keeps main wallet secure (hardware wallet)
2. Isolates gas funding logic
3. Allows rate limiting and monitoring
4. Can be manually approved if needed
5. Audit trail for all gas funding

**For Development/Testing**: Use **manual workaround** (current solution)

**Rationale**:
1. Simple and secure
2. No additional code needed
3. Forces admin awareness of gas needs
4. Good for low-volume operations

## Admin UI Updates Needed

The admin UI should be updated to reflect this limitation:

### Current UI (Misleading)
```
[Fund Gas] button
"Queue gas funding for this address"
```

### Recommended UI (Accurate)
```
[Fund Gas Manually] button
"Gas funding requires manual intervention. Click for instructions."

Modal/Instructions:
1. Send {amount} {chain} to {address}
2. Wait for confirmation
3. Click [Retry Sweep] to resume
```

### Alternative UI (If Implementing Automation)
```
[Fund Gas] button (disabled)
"Automated gas funding not available. Use manual process."

Tooltip: "For security, main wallet key is not accessible to automated systems."
```

## Database Schema

The `manual_actions_log` table already supports `fund_gas` actions:

```sql
CREATE TABLE manual_actions_log (
    id INT PRIMARY KEY AUTO_INCREMENT,
    action_type ENUM('fund_gas', 'retry_sweep', 'send'),
    chain VARCHAR(10),
    address_from VARCHAR(100),
    address_to VARCHAR(100),
    amount DECIMAL(20,8),
    status ENUM('pending', 'success', 'failed'),
    tx_hash VARCHAR(100),
    error_message TEXT,
    created_at TIMESTAMP,
    processed_at TIMESTAMP
);
```

**Note**: Actions are queued but always fail. This is intentional.

## Monitoring fund_gas Failures

Check how many fund_gas actions are failing:

```sql
SELECT 
    DATE(created_at) as date,
    COUNT(*) as total_fund_gas_requests,
    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed,
    SUM(CASE WHEN error_message LIKE '%not implemented%' THEN 1 ELSE 0 END) as not_implemented
FROM manual_actions_log
WHERE action_type = 'fund_gas'
AND created_at > DATE_SUB(NOW(), INTERVAL 30 DAY)
GROUP BY DATE(created_at)
ORDER BY date DESC;
```

Expected result: 100% failed with "not implemented" error

## Summary

| Feature | Status | Workaround |
|---------|--------|------------|
| `fund_gas` action | ❌ Not Implemented | Manual gas funding via external wallet |
| `retry_sweep` action | ✅ Working | Automated |
| `manual_send` action | ✅ Working | Automated |

**Key Takeaway**: `fund_gas` is intentionally disabled for security. Admins must manually fund gas using external wallets. This is a feature, not a bug.

## Related Files
- `manual_actions.py` (line 22-70) - fund_gas implementation
- `sweeper.py` (line 30-40) - Gas balance checking
- `tx_builder.py` - Transaction building (no main wallet key access)

## Contact
For questions about implementing automated gas funding, consult with security team first.
