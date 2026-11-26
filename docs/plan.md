# Build plan: from working prototype to launch

What's left before launch, in order. Each item lists what "done" means and the tests that prove it.
Nothing counts as done until `python run_tests.py` passes and its docs are updated
([testing.md](testing.md#the-process-for-every-change)). Longer-term ideas stay in [roadmap.md](roadmap.md).

**Status:** ⬜ not started · 🔄 in progress · ✅ done

| # | Item | Size | Status |
|---|---|---|---|
| 0 | Test environment: E2E, coverage, CI | Medium | ✅ |
| 1 | API key safety | Small | ⬜ |
| 2 | Dashboard settings: webhooks and integration | Medium | ⬜ |
| 3 | Checkout for shops: name, logo, return link, duplicate protection | Medium | ⬜ |
| 4 | Customer picks the coin; prices in USD/EUR | Large | ⬜ |
| 5 | UI/UX redesign: sign-up, wallet, checkout | Large | ⬜ |
| 6 | Launch prep: BlockCypher hooks, smaller signing engine, deploy, mainnet test | Medium | ⬜ |

Decisions still open are marked **Decide:** and get settled before the item starts.

---

## 0. Test environment ✅

`python run_tests.py`: static checks, backend tests, E2E browser tests against the real frame and a
fake blockchain, independent decoding of every signed transaction, 90% coverage floor, and CI on
every push. See [testing.md](testing.md).

It found a real bug on its first run: **Lock and auto-lock didn't reload the secure frame**, so the
unlocked keys stayed in its memory (fixed; `test_unlock_balances_receive_and_lock` guards it).

---

## 1. API key safety

A leaked API key should only be able to create and read payments, never lock the merchant out or
silence their notifications.

**Done when:**
- `PUT`/`DELETE /accounts/me/webhook` and `POST /accounts/me/api-key` need a **wallet session**
  (signature sign-in). With an API key: **403** with a clear message.
- The dashboard shows when the API key was last used, and the first characters of the current key
  (`gw_Xk3…`), so a merchant can tell which key their shop has.

**Decide:** xpub-only accounts can't sign in to the wallet. Either (a) they keep changing these with
the API key (today's behavior, documented as weaker), or (b) they sign a challenge with their own
wallet's key (Electrum and Sparrow can sign messages). Proposal: (a) now, (b) later.

**Tests:**
- Backend: each of the 3 endpoints, API key → 403 and session → 200; xpub-only account per the decision;
  `last_used_at` updates on use but not more than once a minute (no write on every request).
- E2E: the dashboard shows the key prefix and "last used"; rotating still works from the dashboard.

## 2. Dashboard settings: webhooks and integration

Today webhooks can only be set through the API, and merchants have no guide in the product.

**Done when:**
- **Webhooks screen:** set or remove the URL; the signing secret is shown once with Copy; a
  **Send test event** button (`payment.test` with a sample payment) shows the shop's HTTP status, or
  the error ("timed out", "HTTP 500"); a list of recent deliveries and their status.
- **Integration screen:** the API base URL, the key prefix, and copy-paste samples to create a payment
  and to verify a webhook signature: curl, PHP, Node.js and Python.
- The samples are **tested**: each one runs in CI against the test server.

**Tests:**
- Backend: test-event endpoint (session only, rate-limited, private URLs refused); recent deliveries
  only show the account's own events.
- E2E: set a webhook to a local receiver, send a test event, see "200"; remove it.
- Samples: Python and curl samples run in CI against the E2E server; PHP and Node.js run if the tools are
  installed on the machine.

## 3. Checkout for shops

The payer should see who they're paying and get back to the shop afterwards. Retries mustn't make
two payments.

**Done when:**
- **Shop name** (up to 60 characters) and **logo** (PNG/JPEG/SVG up to 100 KB, stored in the database,
  served from our domain) in the dashboard; shown at the top of the checkout page.
- `POST /payments` takes optional `return_url` (https only, http in development): after paid or
  expired, the checkout page shows **Back to {shop}**.
- **Duplicate protection:** an `Idempotency-Key` header. The same key with the same body within 24 h
  returns the same payment; the same key with a different body gets **409**.

**Decide:** logo upload or logo URL. Proposal: upload, because a URL would load images from other sites
on the checkout page (tracking and privacy, and a CSP exception).

**Tests:**
- Backend: name/logo validation (size, type, SVG scripts refused or the logo is served with a strict
  CSP); `return_url` validation (`javascript:` and `data:` refused, no open redirect: only shown as a
  link the payer clicks); idempotency cases (same body, different body, after 24 h, two at once).
- E2E: the checkout shows name and logo; the "Back to shop" link appears only after paid or expired;
  the phone layout still fits with a long shop name.

## 4. Customer picks the coin; prices in USD/EUR

Shops price in dollars or euros, and customers have different coins.

**Done when:**
- `POST /payments` accepts `{"price": "25.00", "currency": "USD", "accept": ["BTC/BTC", "TRX/USDT", …]}`
  (an amount in crypto still works as today).
- The checkout page first shows **Choose how to pay** (only the coins the shop accepts); choosing one
  converts the price at the current rate, **locks it for 30 minutes**, and shows the address as today.
- Rates come from a public source, cached for 60 s; USDT counts as exactly 1 USD. If no fresh rate is
  available, that coin can't be chosen (never a stale rate).
- The webhook and API show the price, the chosen coin, the rate used and the amount.

**Decide:** rate source. Options: CoinGecko (one call for all coins, free tier rate-limited), Kraken
or Coinbase public tickers (no key, per pair). Proposal: Kraken as primary, Coinbase as fallback; both
have to roughly agree (within 2%) or the coin is unavailable.

**Tests:**
- Backend: conversion and rounding per coin's decimals (never undercharge by rounding); stale or
  disagreeing rates refuse the choice; a choice can't be changed after the address is shown; a
  30-minute lock with the existing expiry and late-payment rules.
- Rate client: recorded answers from both sources, like `test_chain_client.py`.
- E2E: choose a coin, see the converted amount, pay it on the fake chain, get to Paid; on a phone too.

## 5. UI/UX redesign

Sign-up, wallet and checkout should look like one product, and work for people who've never used
crypto.

**Done when:**
- One small design system in `styles.css`: colors (light and dark), type scale, spacing, buttons,
  inputs, status colors. No framework, no build step (CSP stays `script-src 'self'`).
- **Sign-up:** fewer words, clear steps (1 phrase, 2 check, 3 password), and what to do if the phrase is
  lost.
- **Wallet:** sections for Overview (balances, Receive, Send), Payments (search, filter by status),
  Settings (shop, webhooks), Integration; empty states; clear errors.
- **Checkout:** a clear amount and network, a big QR, copy buttons, the status, and progress during
  confirmations; works at 320 px wide.
- Accessibility: keyboard-only use works, visible focus, contrast AA, form labels, `aria-live` status.

**Decide:** a direction before any code. Proposal: 2–3 visual mock-ups of the checkout page first
(the page customers see), pick one, then apply it everywhere.

**Tests:**
- E2E: all existing flows still pass (the redesign must not change behavior); keyboard-only run of
  sign-up and checkout; 320 px and 375 px layouts without sideways scrolling.
- Screenshot comparison of the checkout page states (waiting, partly paid, paid, expired) against
  approved images, so later changes can't break the layout unnoticed.

## 6. Launch prep

**Done when:**
- **BlockCypher push hooks** for BTC/LTC/DOGE: the hook only asks the watcher to check that payment
  now (never trusted as proof), and hooks are removed when payments are final
  ([roadmap.md](roadmap.md#blockcypher-push-hooks)).
- **Smaller signing engine:** `core_proto.js` (1–2 MB) trimmed or compressed, still pinned by SHA-256.
- **Deploy** with [deploy.md](deploy.md) and the **mainnet test** with small amounts, done by the
  merchant.

**Tests:**
- Backend: forged hook calls change nothing; hook clean-up; the watcher falls back to polling if
  hooks stop.
- E2E: Send still works with the smaller engine (the decode checks catch any signing change).
- Manual: the mainnet test checklist in deploy.md, results recorded in the README status table.
