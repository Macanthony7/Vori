"""In-memory blockchain for E2E tests: replaces every chain_client call the API, watcher and wallet use.

Tests set balances / payments with the helpers and read what the wallet relayed from `relayed`.
Nothing here touches the network.
"""
import hashlib

import chain_client


class FakeChain:
    def __init__(self):
        self.reset()

    def reset(self):
        """Empty chain: every test starts without coins left over by another test."""
        self.received = {}      # (chain, asset, address) -> (seen, confirmed), smallest units
        self.balances = {}      # (chain, asset, address) -> current balance (wallet view)
        self.utxos = {}         # (chain, address) -> [{tx_hash, vout, value, confirmations}]
        self.nonces = {}        # (chain, address) -> next nonce
        self.tokens = {}        # (chain, address) -> token balance (USDT smallest units)
        self.tron = {}          # (chain, address) -> {"balance", "active", "bandwidth", "energy"}
        self.relayed = []       # (chain, tx_hex) in relay order
        self.hashes = {}        # (chain, asset, address) -> [tx hash]
        self.down = set()       # addresses whose provider "fails"

    # --- helpers for tests ---------------------------------------------------------------------------
    def pay(self, chain, asset, address, units, confirmed=True, tx="ab" * 32):
        seen, conf = self.received.get((chain, asset, address), (0, 0))
        self.received[(chain, asset, address)] = (seen + units, conf + (units if confirmed else 0))
        if asset == "USDT":
            self.tokens[(chain, address)] = self.tokens.get((chain, address), 0) + units
        else:
            self.balances[(chain, asset, address)] = self.balances.get((chain, asset, address), 0) + units
        self.hashes.setdefault((chain, asset, address), []).append(tx)

    def confirm(self, chain, asset, address):
        seen, _ = self.received[(chain, asset, address)]
        self.received[(chain, asset, address)] = (seen, seen)

    # --- chain_client API ------------------------------------------------------------------------------
    def _check(self, address):
        if address in self.down:
            raise chain_client.ChainError("provider down (fake)")

    def observe(self, chain, asset, address, confirmations):
        self._check(address)
        return self.received.get((chain, asset, address), (0, 0))

    def balance(self, chain, asset, address):
        self._check(address)
        if asset == "USDT":
            return self.tokens.get((chain, address), 0)
        return self.balances.get((chain, asset, address), 0)

    def tx_hashes(self, chain, asset, address, since):
        if chain in ("ETH", "BSC") and asset in ("ETH", "BNB"):
            return None
        return list(self.hashes.get((chain, asset, address), []))

    def unspent(self, chain, address, min_confirmations=1):
        return list(self.utxos.get((chain, address), []))

    def fee_rates(self, chain):
        return {"slow": 2, "normal": 5, "fast": 10}

    def _relay(self, chain, tx_hex):
        self.relayed.append((chain, tx_hex))
        return hashlib.sha256(bytes.fromhex(tx_hex.removeprefix("0x"))).hexdigest()

    def broadcast(self, chain, tx_hex):
        return self._relay(chain, tx_hex)

    def evm_account(self, chain, address):
        native = {"ETH": "ETH", "BSC": "BNB", "SEPOLIA": "ETH"}[chain]
        return self.balances.get((chain, native, address), 0), self.nonces.get((chain, address), 0)

    def evm_fees(self, chain):
        if chain == "BSC":
            return {"type": "legacy", "slow": {"gas_price": "1000000000"}, "normal": {"gas_price": "1000000000"},
                    "fast": {"gas_price": "1200000000"}}
        speed = {"max_fee_per_gas": "20000000000", "max_priority_fee_per_gas": "1000000000"}
        return {"type": "eip1559", "slow": speed, "normal": speed, "fast": speed}

    def evm_broadcast(self, chain, tx_hex):
        return "0x" + self._relay(chain, tx_hex)

    def token_balance(self, chain, contract, address):
        return self.tokens.get((chain, address), 0)

    def evm_token_gas(self, chain, contract, sender, to, amount):
        return 50_000

    def tron_params(self, chain):
        return {"bandwidth_fee": 1000, "energy_fee": 100, "create_fee": 1_100_000}

    def tron_block(self, chain):
        # A real Nile header (from the live checks), so the frame's reference-block math runs for real.
        return {"number": 72971618, "timestamp": 1795457931000,
                "tx_trie_root": "b6023b0ef603b3b725ca8b659cb2b66ac81b7f866958bb353067da62c052496f",
                "parent_hash": "0000000004597561f4e25ffb234e5106902e0c0595344359e5c0f062a447da95",
                "witness_address": "41fdd0fbf29c47124ee8252b8925d455e543d0b0fb", "version": 40}

    def tron_account(self, chain, address):
        if (chain, address) in self.tron:
            return dict(self.tron[(chain, address)])
        trx = self.balances.get((chain, "TRX", address), 0)
        return {"balance": trx, "active": trx > 0, "bandwidth": 0, "energy": 0}

    def tron_token_energy(self, chain, contract, owner, to, amount):
        return 65_000

    def tron_broadcast(self, chain, tx_hex):
        return self._relay(chain, tx_hex)

    def tx_status(self, chain, tx_hash):
        return True  # every relayed transaction "confirms" at once

    def install(self):
        """Point chain_client at this fake. The app and watcher look functions up at call time."""
        for name in ("observe", "balance", "tx_hashes", "unspent", "fee_rates", "broadcast", "evm_account",
                     "evm_fees", "evm_broadcast", "token_balance", "evm_token_gas", "tron_params", "tron_block",
                     "tron_account", "tron_token_energy", "tron_broadcast", "tx_status"):
            setattr(chain_client, name, getattr(self, name))
