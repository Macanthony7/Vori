# Product

<!-- impeccable:product-schema 1 -->

> Written without an interview: the owner told the agent to proceed autonomously through the build plan
> (2026-11-26). Facts below come from the repository (README, docs/, code) and the owner's messages in
> development; lines marked *(inferred)* are the agent's reading and should be confirmed.

## Platform

web

## Users

- **Merchants** running a small online shop or service who want to accept crypto without a custodian:
  they set up an account by creating or importing a wallet, connect their shop's server with an API key and
  webhooks, watch payments arrive, and send funds out from their own device. Often non-experts in crypto
  key handling *(inferred from the onboarding copy: "No email, no personal details, no custody")*.
- **Payers** (the merchant's customers) who land on the checkout page from a shop, often on a phone, pick a
  coin (for USD/EUR prices), scan a QR code or copy an address and amount into their own wallet app, and wait
  for "Paid". They have never seen the product before and arrive mid-purchase.

## Product Purpose

A non-custodial payment gateway that works as a self-custody wallet with merchant features built in. The
server only holds xpubs (public keys); every payment gets a fresh address from the merchant's own wallet; the
recovery phrase lives only in a sandboxed, offline frame on the merchant's device. Success: a merchant can go
from nothing to a paid order without ever trusting anyone with their funds, and a payer can pay correctly on
the first try.

## Positioning

The gateway never holds or can move funds: no sweeper, no hot wallet, no custody. Signing happens on the
merchant's device in an isolated frame whose code is pinned by hash. Privacy-oriented: no email, no personal
data. It is not a mixer and makes no anonymity claims.

## Operating Context

- Merchant dashboard (`wallet.html`): unlock with a password, balances per coin and network, Receive, Send
  (confirmed in the secure frame), New payment, Recent payments, shop name/logo, webhooks, integration samples,
  API key. Used repeatedly, at a desk, alongside the shop's own admin *(inferred)*.
- Checkout (`/pay/{id}`): public, unguessable link, 30-minute window, polls every 5 s, works on phones.
- Sign-up (`index.html` + `derive.html` frame): create a 12-word phrase (with a backup check) or import one,
  or paste xpubs; choose a password that encrypts the phrase on the device.
- Coins: BTC, LTC, DOGE, ETH, BNB (BSC), TRX, USDT on Ethereum/BSC/Tron; Sepolia and Nile test networks.

## Capabilities and Constraints

- Plain HTML/CSS/JS, no framework, no build step for pages; strict CSP (`script-src 'self'`, no inline script
  or style, `img-src 'self' data:`). The seed frame inlines `styles.css` and pins it by SHA-256
  (`frontend/build_frame.py` must be rerun after any `styles.css` change).
- Element ids and visible strings are used by the E2E tests (`tests/e2e/`); behavior must not change in a
  redesign.
- No external fonts, images or scripts may be loaded (CSP and privacy): system font stacks only.
- QR codes stay black on white (scanner reliability).

## Evidence on Hand

No logo, brand name, testimonials, customers or press exist. The product has no name yet ("payment
gateway"; the GitHub repo is called Vori, *unconfirmed as a product name*). Nothing may be invented.

## Product Principles

1. Never imply custody or safety the product doesn't provide; say plainly what happens to keys and funds.
2. The payer must not make a mistake: amount, coin and network are unmissable; wrong-network warnings stay.
3. Every irreversible step (sending funds, replacing a key) is confirmed with what will happen.
4. Plain words over crypto jargon; explain terms where they appear.

## Accessibility & Inclusion

WCAG 2.1 AA contrast, full keyboard use, visible focus, labels on every control, live status for payment
progress *(the plan's requirement, docs/plan.md item 5)*.
