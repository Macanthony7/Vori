# Build plan: from working prototype to launch

What's left before launch, in order. Each item lists what "done" means and the tests that prove it.
Nothing counts as done until `python run_tests.py` passes and its docs are updated
([testing.md](testing.md#the-process-for-every-change)). Longer-term ideas stay in [roadmap.md](roadmap.md).

**Status:** ⬜ not started · 🔄 in progress · ✅ done

| # | Item | Size | Status |
|---|---|---|---|
| 0 | Test environment: E2E, coverage, CI | Medium | ✅ |
| 1 | API key safety | Small | ✅ |
| 2 | Dashboard settings: webhooks and integration | Medium | ✅ |
| 3 | Checkout for shops: name, logo, return link, duplicate protection | Medium | ✅ |
| 4 | Customer picks the coin; prices in USD/EUR | Large | ✅ |
| 5 | UI/UX redesign: sign-up, wallet, checkout | Large | ✅ |
| 6 | Launch prep: BlockCypher hooks, smaller signing engine, deploy, mainnet test | Medium | 🔄 (code done; deploy and mainnet test are yours) |
| 7 | Gem-style layout (wallet, sign-up, checkout); extension and phone app plan | Large | ✅ |

Decisions still open are marked **Decide:** and get settled before the item starts.

---

## 0. Test environment ✅

`python run_tests.py`: static checks, backend tests, E2E browser tests against the real frame and a
fake blockchain, independent decoding of every signed transaction, 90% coverage floor, and CI on
every push. See [testing.md](testing.md).

It found a real bug on its first run: **Lock and auto-lock didn't reload the secure frame**, so the
unlocked keys stayed in its memory (fixed; `test_unlock_balances_receive_and_lock` guards it).

---

## 1. API key safety ✅

Built as planned. **Decided:** (a). The rule is "once the account has signed in to the wallet": from
the first wallet sign-in on, settings need a session; accounts that never sign in (xpub-only) keep
using the API key. Tests: `check_wallet_login` in `backend/test_payments.py`,
`test_api_key_rotation_hint_and_settings_protection` in `tests/e2e/test_wallet_checkout.py`.

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

## 2. Dashboard settings: webhooks and integration ✅

Built as planned: Webhooks screen (save, secret once, test event with the shop's answer, recent
deliveries, remove) and Integration screen (steps, API address, samples). All four samples run in the
tests (PHP and curl when installed). Tests: `backend/test_payments.py` (test event, deliveries),
`tests/e2e/test_settings.py`, `tests/e2e/test_samples.py`.

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

## 3. Checkout for shops ✅

Built as planned. **Decided:** upload (not a URL), PNG/JPEG only (SVG refused instead of sandboxed: simpler and safer). The test suite caught that the security-headers middleware overwrote every route's CSP, so the logo's strict policy was lost; fixed. Tests: `check_shop_and_idempotency` in `backend/test_payments.py` (incl. two retries at the same moment), `tests/e2e/test_shop.py`.

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

## 4. Customer picks the coin; prices in USD/EUR ✅

Built as planned. **Decided:** Kraken + Coinbase; checked live, Coinbase doesn't list TRX, so a coin listed
by only one source uses it, and where both list it they must agree within 2%. Prices are stored with 2
decimals. Tests: `backend/test_rates.py`, `check_price_requests` in `backend/test_payments.py` (incl. two
choices at once), `tests/e2e/test_price.py`. While writing them, backend tests turned out to depend on the
developer's `backend/.env` (test networks on); `backend/conftest.py` now fixes the settings.

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

## 5. UI/UX redesign ✅

Built with the Impeccable design skill, unattended (the owner said to keep going), so the skill's seeded
direction was taken without a mock-up round. **Decided:** "assay hallmarks and the touchstone": a deep
green-black shell, a cool sterling ground, gold only where something is proven, and small double-ruled
hallmarks for coin, network and state; payment progress is struck as hallmarks (Waiting, Seen, Confirmed).
Records: `PRODUCT.md`, `.impeccable/surfaces/` (direction contract), `DESIGN.md`. The wallet is now in tabs
(Overview, Payments with search and filter, Settings, Integration); sign-up shows its steps; the checkout
has one headline (the amount). Tests: `tests/e2e/test_design.py` (keyboard-only sign-up and tabs, focus
ring, 320/375 px with no sideways scrolling on every page, search and filter, screenshot comparison of the
checkout states in light and dark); every earlier E2E test passes unchanged in behavior (they now open tabs
instead of collapsible sections).

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

## 6. Launch prep 🔄

**Built:** BlockCypher push hooks ([roadmap.md](roadmap.md#blockcypher-push-hooks)) and a smaller signing
engine download. Measured, the engine was 21.3 MB (`core_proto.js` 10.8 MB and `wallet-core.wasm` 10.4 MB,
not the 1-2 MB first assumed); trimming the vendored code would mean modifying it, so it's **compressed**
instead: 4.7 MB with gzip (the app's `GZipMiddleware`, and Caddy's `encode gzip` in production). The frame
hashes the decompressed bytes, so the SHA-256 pins are unchanged. Tests: `backend/test_hooks.py`.
**Left, needing the owner:** the deploy ([deploy.md](deploy.md)), then the mainnet test with small amounts,
and a one-time check of the hooks on staging with a real BlockCypher token.

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

## 7. Gem-style layout ✅

**Asked:** keep the colours of item 5, take the layout and features of [Gem Wallet](https://github.com/gemwalletcom/wallet)
(an open-source phone wallet), no code from it (it's GPL; we read its App Store screenshots for structure
only, and use none of its assets, icons or logo).

**Built:**
- **Wallet in three tabs**, Wallet · Payments · Settings, each a stack of screens with Back. Phone: a
  floating tab bar; desktop: a sidebar. Integration moved into Settings → Developer.
- **Wallet home:** total balance in USD with today's change, round actions (Send, Receive, New payment,
  Find late), every coin as a row with price and 24 h change, balance and value.
- **Coin screen:** balance, actions, price chart (1H/1D/1W/1M/1Y), price, network, main address, the coin's payments.
- **Payments** grouped by day with search and status chips; a **payment detail** screen.
- **Send** with a large amount entry and its value in USD; **New payment** as its own screen.
- **Sign-up:** a welcome screen with two wide buttons; the phrase in a numbered 3-column grid; the check is
  now "tap the right word" for three positions (three words each, all from the phrase), instead of typing.
- **Checkout** in the same language: grouped list for address, amount and time left; coin marks on the choices.
- **Market data** for display: new `GET /market` and `GET /market/{coin}/history` ([api.md](api.md#market-data)),
  Kraken's public candles, cached; never used to price a payment.
- Coin logos from the *cryptocurrency-icons* set (CC0) in `frontend/public/coins/`, with a coloured
  disc and the coin's sign as the fallback.

**Tests:** every E2E test drives the new screens (helpers `tab`, `screen`, `open_coin`, `new_payment` in
`tests/e2e/conftest.py`); market data is faked in the E2E tests so they never reach the internet; new
`backend/test_market.py` and a history/24 h change test in `backend/test_rates.py`; the 320/375 px checks
now cover every Settings screen, the coin screen and the tab bar; checkout goldens re-recorded.

### Extension and phone app (decided now, built later)
- **One design system for all three.** The extension popup (360×600) and the phone app use the web
  wallet's phone layout exactly: same screens, tokens and components ([DESIGN.md](../DESIGN.md#layout)).
- **Browser extension:** the same pages as a Manifest V3 extension. The phrase frame becomes the
  extension's sandboxed page (`"sandbox"` in the manifest), so it stays offline and isolated as today;
  the popup is the phone layout.
- **Phone app:** first make the web wallet installable (PWA: manifest and icons, no offline payments).
  A store app later wraps the same code; the vault moves to the phone's secure storage (Keychain /
  Android Keystore), unlocked with Face ID or fingerprint.
- Not started: each needs its own plan item with tests when it's next.

