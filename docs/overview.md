# Overview

## What it is

A crypto payment gateway for merchants who want to accept payments without handing over an email,
an identity or their funds.

- **No personal data.** An account is an API key plus the merchant's public keys. No email, name or password.
- **Non-custodial.** The service never holds funds and never holds a key that can spend them.
  Customers pay to addresses that belong to the merchant's own wallet.
- **Per-payment addresses.** Each payment gets a fresh address derived from the merchant's xpub, so
  payments are easy to match and addresses aren't reused.

It's not a mixer and doesn't hide anything on-chain. Payments are as traceable as any normal
transaction. The privacy claim is only that we don't collect personal data.

## How a merchant signs up

The onboarding page offers three paths. All three end with the same thing: the server receives
account xpubs and returns an API key.

1. **Create a new wallet.** The browser generates a 12-word recovery phrase. The merchant writes it
   down and confirms 3 random words. The phrase can be imported into any BIP39 wallet (Trust Wallet,
   MetaMask, Electrum and so on) to spend the funds.
2. **I have a recovery phrase.** The merchant pastes an existing phrase.
3. **Use xpub keys.** For hardware wallet users or anyone who doesn't want to type a phrase into a
   web page: paste xpubs exported from their wallet.

For paths 1 and 2 the phrase is handled only inside an isolated, offline frame. See
[security.md](security.md).

## How a payment works

1. The merchant's server calls `POST /payments` with the chain, the asset and the amount, for example `TRX` / `USDT` / `"12.50"`.
2. The gateway returns a fresh address from the merchant's own wallet, and the customer has **30 minutes** to pay.
3. The watcher checks the chain. Once the full amount has the standard number of confirmations, the payment is `confirmed` and the merchant gets a signed webhook.

Supported: BTC, LTC, DOGE, ETH, BNB (BSC), TRX, and **USDT on Ethereum, BSC and Tron**.
Details: [api.md](api.md#post-payments).

## How we make money

Freemium with prepaid plans (planned, not built yet): a free tier with a monthly payment cap, and
paid plans that the merchant pays for through the gateway itself, extending `plan_expires_at`.
There's nothing to chase: an expired plan simply falls back to free-tier limits.

## Glossary

| Term | Meaning |
|---|---|
| Recovery phrase / seed phrase | 12 to 24 BIP39 words that control a wallet. Whoever has it can spend everything. We never receive it. |
| xpub (extended public key) | A public key that can generate every receiving address of one wallet account, but can't spend. |
| Account xpub | The xpub at path `m/44'/coin'/account'` (BIP44). The only kind we accept. |
| Account number | The `account'` part of the path. New wallets use 0; imported phrases default to 1 so payments stay apart from the wallet's main account. |
| `Ltub`, `dgub` | Litecoin's and Dogecoin's own prefixes for an xpub. Accepted and normalized. |
| API key | The merchant's only credential, `gw_…`. Shown once; we store only its SHA-256 hash. |
| Payment | A request for a specific amount of one asset, with its own address and a 30-minute window. Id `pay_…`. |
| Confirmations | How many blocks have been added on top of the payment's block. More means harder to reverse. |
| Gap limit | How many unused addresses in a row a wallet scans before it stops looking (usually 20). |
| Webhook | An HTTP POST the gateway sends to the merchant's server when a payment changes status, signed with the webhook secret. |
