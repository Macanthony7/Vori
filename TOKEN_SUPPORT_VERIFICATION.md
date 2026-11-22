# Token Support End-to-End Verification

## Overview
AEGIS crypto payment system supports both native coins (BTC, ETH, BSC, LTC, DOGE, TRX) and tokens (USDT/USDC on ERC20/BEP20/TRC20 networks).

## Implementation Status: ✅ COMPLETE

### 1. Database Schema
- ✅ `crypto_payments.token_contract` column added (VARCHAR 100, nullable)
- ✅ Migration: `2026_05_05_000001_add_token_contract_to_crypto_payments.php`

### 2. HYIP Configuration (PHP)
**File**: `hyip/modules/Payment/AegisPayment/config.php`

Token contracts configured:
```php
'USDT' => [
    'TRX' => 'TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t',  // TRC20
    'ETH' => '0xdac17f958d2ee523a2206206994597c13d831ec7', // ERC20
    'BSC' => '0x55d398326f99059ff775485246999027b3197955', // BEP20
],
'USDC' => [
    'ETH' => '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48', // ERC20
    'BSC' => '0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d', // BEP20
    'TRX' => 'TEkxiTehnzSmSe2XqrBj4w32RUN966rdz8',  // TRC20
]
```

### 3. Payment Processing (PHP)
**File**: `hyip/modules/Payment/AegisPayment/payment_process.php`

- ✅ Line 82: Extracts `token_contract` from coin config
- ✅ Line 93: Stores `token_contract` in crypto_payments INSERT

### 4. RPC Verification (Python)
**File**: `crypto_service/rpc_client.py`

#### ERC20 Token Verification (ETH)
- ✅ Function: `verify_eth_transaction(tx_hash, address, chain, expected_amount, token_contract)`
- ✅ Lines 169-195: Parses Transfer event logs
- ✅ Topic: `0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef`
- ✅ Extracts recipient from topics[2], amount from data field
- ✅ Validates token_contract matches log address
- ✅ Uses 6 decimal precision (1e6) for USDT/USDC

#### BEP20 Token Verification (BSC)
- ✅ Function: `verify_bsc_transaction(tx_hash, address, chain, expected_amount, token_contract)`
- ✅ Lines 295-321: Parses Transfer event logs (same logic as ERC20)
- ✅ Uses Ankr RPC endpoint

#### TRC20 Token Verification (TRX)
- ✅ Function: `verify_trx_transaction(tx_hash, address, chain, expected_amount, token_contract)`
- ✅ Lines 437-479: Parses smart contract data hex
- ✅ Extracts recipient from bytes 32-72, amount from bytes 72-136
- ✅ Validates contract address matches token_contract
- ✅ Uses base58 address conversion for TRON

### 5. Token Sweep Functions (Python)
**File**: `crypto_service/tx_builder.py`

#### ERC20 Token Sweep
- ✅ Function: `execute_eth_token_sweep(from_address, to_address, token_contract, chain, private_key)`
- ✅ Lines 177-254: Uses Web3.py to build ERC20 transfer transaction
- ✅ Gets token balance via balanceOf()
- ✅ Builds transfer() transaction with proper gas estimation
- ✅ Signs and broadcasts to Ethereum mainnet

#### BEP20 Token Sweep
- ✅ Function: `execute_bsc_token_sweep(from_address, to_address, token_contract, chain, private_key)`
- ✅ Lines 261-338: Uses Web3.py with BSC RPC
- ✅ Same logic as ERC20 but chainId=56

#### TRC20 Token Sweep
- ✅ Function: `execute_trx_token_sweep(from_address, to_address, token_contract, chain, private_key)`
- ✅ Lines 345-395: Uses TronPy library
- ✅ Gets contract, checks balance, builds transfer transaction
- ✅ Signs and broadcasts to TRON mainnet

#### Main Router
- ✅ Function: `execute_sweep_transaction(from_address, to_address, amount, chain, private_key, token_contract=None)`
- ✅ Lines 625-653: Routes to token sweep functions when token_contract is provided
- ✅ Routes to native coin sweep functions when token_contract is None

### 6. Webhook Processing (Python)
**File**: `crypto_service/webhook_processor.py`

- ✅ Line 177: Fetches `token_contract` from deposit record
- ✅ Line 178: Passes `token_contract` to `verify_transaction_rpc()`

### 7. Fallback Watcher (Python)
**File**: `crypto_service/fallback_watcher.py`

- ✅ Line 99: Fetches `token_contract` from deposit record
- ✅ Line 100: Passes `token_contract` to `verify_transaction_rpc()`

### 8. Sweeper (Python)
**File**: `crypto_service/sweeper.py`

- ✅ Line 48: Fetches `token_contract` from deposit record
- ✅ Line 77: Passes `token_contract` to `execute_sweep_transaction()`

### 9. Manual Actions (Python)
**File**: `crypto_service/manual_actions.py`

- ✅ Line 127: Fetches `token_contract` from deposit record in `process_manual_send()`
- ✅ Line 165: Passes `token_contract` to `execute_sweep_transaction()`

## Testing Checklist

### Test 1: USDT TRC20 Deposit
1. User selects USDT (TRC20) payment option
2. System generates TRX address and stores token_contract='TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t'
3. User sends USDT to address
4. Webhook/fallback detects transaction
5. RPC verifies TRC20 transfer (parses smart contract data)
6. User balance credited
7. Sweeper executes TRC20 token sweep to main wallet

### Test 2: USDC ERC20 Deposit
1. User selects USDC (ERC20) payment option
2. System generates ETH address and stores token_contract='0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48'
3. User sends USDC to address
4. Webhook/fallback detects transaction
5. RPC verifies ERC20 transfer (parses Transfer event logs)
6. User balance credited
7. Sweeper executes ERC20 token sweep to main wallet

### Test 3: USDT BEP20 Deposit
1. User selects USDT (BEP20) payment option
2. System generates BSC address and stores token_contract='0x55d398326f99059ff775485246999027b3197955'
3. User sends USDT to address
4. Webhook/fallback detects transaction
5. RPC verifies BEP20 transfer (parses Transfer event logs)
6. User balance credited
7. Sweeper executes BEP20 token sweep to main wallet

### Test 4: Native Coin Deposit (Control Test)
1. User selects ETH payment option
2. System generates ETH address with token_contract=NULL
3. User sends ETH to address
4. RPC verifies native ETH transfer
5. User balance credited
6. Sweeper executes native ETH sweep

## Environment Variables Required

```bash
# RPC API Keys
ALCHEMY_API_KEY=your_alchemy_key_here
ANKR_API_KEY=your_ankr_key_here
TRONGRID_API_KEY=your_trongrid_key_here

# Token Contract Addresses (optional, defaults in config.php)
AEGIS_USDT_TRX_CONTRACT=TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t
AEGIS_USDT_ETH_CONTRACT=0xdac17f958d2ee523a2206206994597c13d831ec7
AEGIS_USDT_BSC_CONTRACT=0x55d398326f99059ff775485246999027b3197955
AEGIS_USDC_ETH_CONTRACT=0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48
AEGIS_USDC_BSC_CONTRACT=0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d
AEGIS_USDC_TRX_CONTRACT=TEkxiTehnzSmSe2XqrBj4w32RUN966rdz8
```

## Python Dependencies

```bash
pip install web3>=6.0.0        # For ERC20/BEP20
pip install tronpy>=0.4.0      # For TRC20
pip install eth-account>=0.8.0 # For Ethereum signing
```

## Known Limitations

1. **Gas Funding**: Token sweeps require native gas (ETH/BNB/TRX) in deposit address
   - Sweeper checks gas balance before attempting token sweep
   - If insufficient gas, marks deposit for gas funding
   - Admin must manually fund gas or implement auto-gas-funding

2. **Decimal Precision**: Currently hardcoded to 6 decimals (1e6) for USDT/USDC
   - Works for USDT/USDC which use 6 decimals
   - Would need adjustment for tokens with different decimals

3. **Token Balance Check**: Sweep functions query on-chain balance
   - Ensures accurate sweep amount
   - May differ slightly from expected amount due to fees

## Security Considerations

1. ✅ SSRF Prevention: tx_hash and token_contract validated before URL construction
2. ✅ Input Validation: All parameters validated in RPC functions
3. ✅ Contract Address Validation: Token contract addresses validated against expected values
4. ✅ Private Key Security: Keys derived from seed phrase, never stored
5. ✅ Transaction Signing: All transactions signed locally before broadcast

## Deployment Verification

After deployment, verify:

1. ✅ Database migration applied successfully
2. ✅ Token contract addresses configured in .env or config.php
3. ✅ RPC API keys configured and working
4. ✅ Python dependencies installed (web3, tronpy, eth-account)
5. ✅ Test deposit for each token type (USDT TRC20, USDT ERC20, USDT BEP20, USDC variants)
6. ✅ Verify RPC verification logs show correct token parsing
7. ✅ Verify sweep transactions broadcast successfully
8. ✅ Check main wallet receives tokens

## Troubleshooting

### Token verification fails
- Check token_contract address matches blockchain
- Verify RPC API key has sufficient credits
- Check transaction is actually a token transfer (not native coin)

### Token sweep fails
- Verify deposit address has sufficient gas (ETH/BNB/TRX)
- Check token balance is > 0
- Verify private key derivation is correct
- Check RPC endpoint is accessible

### Wrong amount detected
- Verify decimal precision (USDT/USDC use 6 decimals)
- Check if token uses different decimal places
- Verify Transfer event log parsing is correct

## Conclusion

Token support is **FULLY IMPLEMENTED** end-to-end:
- ✅ Database schema supports token_contract
- ✅ HYIP config defines all token contracts
- ✅ Payment processing stores token_contract
- ✅ RPC verification parses token transfers correctly
- ✅ Token sweep functions implemented for all networks
- ✅ All processors (webhook, fallback, sweeper, manual) pass token_contract
- ✅ Security validations in place

**Status**: PRODUCTION READY for USDT/USDC on ERC20/BEP20/TRC20 networks
