---
name: Self-custody payments
description: A non-custodial crypto payment gateway drawn as an assay office; touchstone shell, sterling ground, gold only where something is proven.
colors:
  ground: "#eef1ef"
  surface: "#ffffff"
  surface-2: "#f5f7f6"
  ink: "#16201c"
  muted: "#4b5853"
  line: "#c9d2ce"
  line-strong: "#8e9c96"
  stone: "#1b2622"
  stone-2: "#27352f"
  on-stone: "#e9efec"
  on-stone-muted: "#a8b7b1"
  gold: "#b98419"
  gold-ink: "#7a560c"
  gold-bg: "#f8edd2"
  link: "#1c6650"
  action: "#1b2622"
  action-hover: "#2d3d36"
  action-text: "#f3f6f4"
  ok: "#1d6a44"
  ok-bg: "#e2f1e8"
  wait: "#855600"
  wait-bg: "#fbefd6"
  warning-text: "#5a3b00"
  bad: "#a3271f"
  bad-bg: "#f9e4e1"
  qr-field: "#ffffff"
typography:
  display:
    fontFamily: "\"Segoe UI Variable Text\", \"Segoe UI\", system-ui, -apple-system, BlinkMacSystemFont, Roboto, \"Helvetica Neue\", Arial, sans-serif"
    fontSize: "2.566rem"
    fontWeight: 700
    lineHeight: 1.1
    letterSpacing: "-0.02em"
    fontFeature: "tnum"
  headline:
    fontFamily: "\"Segoe UI Variable Text\", \"Segoe UI\", system-ui, -apple-system, BlinkMacSystemFont, Roboto, \"Helvetica Neue\", Arial, sans-serif"
    fontSize: "1.802rem"
    fontWeight: 700
    lineHeight: 1.15
    letterSpacing: "-0.01em"
  title:
    fontFamily: "\"Segoe UI Variable Text\", \"Segoe UI\", system-ui, -apple-system, BlinkMacSystemFont, Roboto, \"Helvetica Neue\", Arial, sans-serif"
    fontSize: "1.266rem"
    fontWeight: 650
    lineHeight: 1.25
    letterSpacing: "-0.01em"
  title-sm:
    fontFamily: "\"Segoe UI Variable Text\", \"Segoe UI\", system-ui, -apple-system, BlinkMacSystemFont, Roboto, \"Helvetica Neue\", Arial, sans-serif"
    fontSize: "1.125rem"
    fontWeight: 650
    lineHeight: 1.3
    letterSpacing: "-0.01em"
  body:
    fontFamily: "\"Segoe UI Variable Text\", \"Segoe UI\", system-ui, -apple-system, BlinkMacSystemFont, Roboto, \"Helvetica Neue\", Arial, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.55
    fontFeature: "tnum"
  label:
    fontFamily: "\"Segoe UI Variable Text\", \"Segoe UI\", system-ui, -apple-system, BlinkMacSystemFont, Roboto, \"Helvetica Neue\", Arial, sans-serif"
    fontSize: "0.889rem"
    fontWeight: 600
    lineHeight: 1.55
  mark:
    fontFamily: "\"Segoe UI Variable Text\", \"Segoe UI\", system-ui, -apple-system, BlinkMacSystemFont, Roboto, \"Helvetica Neue\", Arial, sans-serif"
    fontSize: "0.79rem"
    fontWeight: 700
    lineHeight: 1.5
    letterSpacing: "0.02em"
  mono:
    fontFamily: "ui-monospace, \"Cascadia Mono\", \"SF Mono\", SFMono-Regular, Menlo, Consolas, monospace"
    fontSize: "0.889rem"
    fontWeight: 400
    lineHeight: 1.4
rounded:
  mark: "3px"
  focus: "4px"
  control: "6px"
  card: "8px"
spacing:
  s1: "4px"
  s2: "8px"
  s3: "12px"
  s4: "16px"
  s5: "24px"
  s6: "32px"
  s7: "48px"
components:
  button-primary:
    backgroundColor: "{colors.action}"
    textColor: "{colors.action-text}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "8px 16px"
    height: "44px"
  button-primary-hover:
    backgroundColor: "{colors.action-hover}"
    textColor: "{colors.action-text}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "8px 16px"
    height: "44px"
  button-secondary-hover:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.ink}"
  button-link:
    textColor: "{colors.link}"
    padding: "0"
  button-compact:
    rounded: "{rounded.control}"
    padding: "4px 12px"
    height: "36px"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "8px 12px"
    height: "44px"
  input-readonly:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.ink}"
    typography: "{typography.mono}"
  mark:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.muted}"
    typography: "{typography.mark}"
    rounded: "{rounded.mark}"
    padding: "1px 8px"
  mark-confirmed:
    textColor: "{colors.ok}"
  mark-pending:
    textColor: "{colors.ink}"
  mark-expired:
    textColor: "{colors.bad}"
  mark-proven:
    backgroundColor: "{colors.gold-bg}"
    textColor: "{colors.gold-ink}"
  card:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.card}"
    padding: "24px"
  shell:
    backgroundColor: "{colors.stone}"
    textColor: "{colors.on-stone}"
    height: "60px"
  tab:
    textColor: "{colors.on-stone-muted}"
    typography: "{typography.label}"
    padding: "0 12px"
  tab-selected:
    textColor: "{colors.on-stone}"
  shop-band:
    backgroundColor: "{colors.stone}"
    textColor: "{colors.on-stone}"
    rounded: "{rounded.card}"
    padding: "12px 16px"
  code-sample:
    backgroundColor: "{colors.stone}"
    textColor: "{colors.on-stone}"
    typography: "{typography.mono}"
    rounded: "{rounded.control}"
    padding: "16px"
---

# Design System: Self-custody payments

## Overview

**Creative North Star: "The Assay Office"**

At an assay office, metal is rubbed on a touchstone and then struck with small hallmarks that certify what it is. This system works the same way. A deep green-black touchstone holds the frame: the shell bar, the shop band on checkout, primary actions and code samples. Pages sit on a cool sterling ground. The gold streak a touchstone leaves is the one bright note, and it marks only what has been proven: a confirmed payment, the selected tab, the step you are on, the focus ring. State (coin, network, payment status, sign-up step) is carried by small framed cartouches with a double rule, as if punched into the page.

Density is calm and working. The wallet is a desk tool with rows, tables and cards on a 1040px canvas. The checkout is a single 480px column where the amount owns the field. Sign-up is a 640px column, plus a sandboxed frame that shares the same stylesheet. One system sans is used throughout with tabular numerals. Mono appears only where people read character by character: addresses, keys, phrases, secrets and code.

The system rejects the neon-on-black crypto dashboard and the grey admin template with a single blue accent.

**Key Characteristics:**
- Touchstone dark field for the shell and primary action; sterling ground; white surfaces with hairline borders.
- Gold reserved for proof and the active position, never for decoration or warnings.
- Hallmark cartouches (3px corners, 1.5px outer rule plus inset inner rule) carry every status.
- One sans family stepped at 1.125; tabular numerals everywhere; mono only for strings people copy.
- A strict 4px spacing armature.
- System fonts only, nothing loaded from another origin, no inline style or script (strict CSP).
- Light and dark schemes follow `prefers-color-scheme`; the QR code stays black on white in both.

## Colors

The palette is cool green-greys around one warm metal: a near-black touchstone, a pale sterling ground, and gold used sparingly.

### Primary
- **Touchstone** (`stone`, also `action`): the shell bar, the checkout's shop band, primary buttons, the pressed integration tab and the code-sample field. In dark mode, `action` flips to the pale ink so primary buttons stay the brightest control on a dark ground, while `stone` gets darker still.
- **Touchstone Raised** (`stone-2`): hover on shell controls, the body of the brand emblem, scrollbars inside code samples.
- **Pressed Touchstone** (`action-hover`): primary button hover.

### Secondary
- **Assay Gold** (`gold`): the streak. It is used for the selected-tab underline, the brand emblem stripe and the text caret. `focus` uses the same value.
- **Struck Gold Ink** (`gold-ink`) on **Gold Leaf** (`gold-bg`): a proven hallmark (confirmed payment, current sign-up step), the number markers in integration steps, and text selection (`gold-bg`).

### Tertiary
- **Verdigris Link** (`link`): inline links, link-style buttons and `<summary>` disclosures.

### Neutral
- **Sterling** (`ground`): the page ground.
- **Assay Paper** (`surface`) and **Pale Sterling** (`surface-2`): cards, sheets, inputs; read-only fields, table headers and row hover use `surface-2`.
- **Touchstone Ink** (`ink`) and **Worn Ink** (`muted`): body text; secondary text, hints and unstruck marks. `muted` passes WCAG AA on every surface.
- **Hairline** (`line`) and **Scribe Line** (`line-strong`): card and table borders; input and secondary-button borders and the dashed empty state.
- **On Stone** (`on-stone`, `on-stone-muted`): text on the touchstone field, with unselected tabs in the muted variant.

### Semantic
- **Confirmed** (`ok` on `ok-bg`), **Waiting** (`wait` on `wait-bg`, warnings use `warning-text`), **Failed / Expired** (`bad` on `bad-bg`). Status tones always come as text-on-tint pairs; never use the tint alone.
- **QR Field** (`qr-field`): pure white behind QR codes and shop logos in every scheme.

Dark mode redefines every token under `prefers-color-scheme: dark` (values in `.impeccable/design.json` under `colorMeta.*.dark`). No component carries its own dark override except the checkout shop band, which adds a hairline edge so the stone band doesn't merge with the dark ground.

### Named Rules
**The Proven Gold Rule.** Gold (`gold`, `gold-ink`, `gold-bg`) means proven or "you are here": a confirmed payment, the selected tab, the current sign-up step, the checkout strip's current stage, focus and selection. Pending and in-flight states use ink, not gold. Warnings use the amber `wait` family, which is a different color from the gold.

**The Black-on-White QR Rule.** QR codes render black on `qr-field` with a 12px quiet zone and a hairline border, including in dark mode. Some scanners fail on inverted codes.

## Typography

**Display Font:** System UI sans ("Segoe UI Variable Text", falling back through system-ui, -apple-system, Roboto, Helvetica Neue and Arial)
**Body Font:** the same stack
**Label/Mono Font:** ui-monospace (falling back through Cascadia Mono, SF Mono, Menlo and Consolas), for addresses, keys, phrases, secrets and code only

**Character:** One family repeated on a strict 1.125 scale. Hierarchy comes from size and weight (400 / 600 / 650 / 700) and tight negative tracking on headings, never from a second voice.

### Hierarchy
- **Display** (700, 2.566rem, 1.1, -0.02em): the checkout amount only, with the coin unit at 0.55em in muted 600. Drops to the headline size below 380px.
- **Headline** (700, 1.802rem, 1.15): page titles in sign-up.
- **Title** (650, 1.266rem, 1.25): section heads, card and sheet titles.
- **Title small** (650, 1.125rem, 1.3): sub-sections, balance asset names and amounts, option titles, the countdown figure. 1.125rem is also the lede size.
- **Body** (400, 1rem, 1.55): running text, kept to 60ch in ledes and 52ch in empty states. Tabular numerals are on from `body` down.
- **Label** (600, 0.889rem): form labels, hints and notes (notes at 400 muted), table text.
- **Mark** (700, 0.79rem, 0.02em, sentence case): hallmark text. Table column headers use the same size at 600, uppercase, 0.04em tracking, muted.

### Named Rules
**The One Voice Rule.** Sizes come only from the eight-step scale (0.79, 0.889, 1, 1.125, 1.266, 1.424, 1.802, 2.566rem). No second family, no webfonts.

**The Mono Earns Its Place Rule.** Monospace is used only for strings a person reads or compares character by character: addresses, xpubs, recovery words, webhook secrets and code samples.

**The One Headline Rule.** On checkout, the amount is the only display-size element. Everything else stays at title size or below.

## Layout

Every gap, padding and margin is a multiple of 4px, taken from the seven-step scale (`s1` to `s7`). The only exceptions are hairline borders and the mark's 1px vertical padding.

**Structure (from Gem Wallet's layout, our own code and palette).** The wallet is three tabs, **Wallet · Payments · Settings**, and each tab is a stack of screens: a list screen first, and detail screens pushed on top of it with a Back button in a top bar. Only one screen per tab shows at a time; tapping the open tab again returns to its first screen.

- **Phone (below 900px):** one 560px column, and the tabs float at the bottom as a touchstone pill (icon over label; the selected tab's icon is gold). The page keeps 120px of bottom padding so the pill never covers the last row.
- **Desktop (900px and up):** the same tabs as a 240px touchstone sidebar (brand, tabs, Lock at the bottom) with a 720px content column. Lock in the wallet's top bar is hidden there.
- **Top bar:** a three-column grid (44px | title | 44px): Back or a round icon button on the left, the centred title (or the wallet pill), a round icon button or an empty 44px slot on the right.
- **Wallet home:** the total balance as the one headline (display size, in USD, testnets never counted), today's change under it (green up, red down), four round actions (Send, Receive, New payment, Find late), then every coin as one grouped list.
- **Coin screen:** coin mark (64px), balance and its value, the actions, a chart card (line over a pale area, 1H/1D/1W/1M/1Y segmented control), a grouped list (Price, Network, Main address on its own line), then that coin's payments.
- **Send / New payment:** a large centred amount entry with the unit beside it, a grouped form list, the note, and one wide pill button.
- **Payments:** a pill search field, status chips, then grouped lists under day headings (Today, Yesterday, 12 Nov). A row opens the payment's detail screen.
- **Settings:** grouped lists under section titles (Shop; Developer: API key, Webhooks, Connect your shop; Security). Rows open their screens.
- **Sign-up:** a centred welcome (72px emblem, headline, promise, wide pill buttons: Create a new wallet, Import an existing wallet, and a quiet xpub link), then the secure frame.
- **Checkout:** stacks in order: shop band, amount, network mark, hallmark strip, status, QR card, a grouped list (address, amount, time left), network warning, back link. At 380px, padding tightens to 12px sides and 16px card padding.
- Touch targets are 44px minimum height (36px for chips and segmented buttons).

**The Whole Rows Rule.** Rows that hold live entities (coins, payments, deliveries) repack as whole rows at every width. A status change restyles the row in place and never moves the list.

**One layout for every platform.** The browser extension popup (360×600) and the phone app reuse the phone layout of the web wallet exactly: same screens, same tokens, same components. Platform differences stay in how keys are stored, never in how screens look ([docs/plan.md](docs/plan.md#7-gem-style-layout)).

## Elevation & Depth

Surfaces are mostly flat, with depth from tonal layering: the sterling ground, white surfaces with hairline borders, and pale-sterling insets. Shadows are soft and ambient, with large blur and negative spread tinted with touchstone (rgba(16, 26, 22, ...)), and appear only on raised working surfaces. There are no hard offset shadows.

### Shadow Vocabulary
- **Hover lift** (`0 4px 14px -6px rgba(16,26,22,0.28)` with a 1px rise): sign-up option cards on hover.
- **Frame** (`0 6px 24px -14px rgba(16,26,22,0.35)`): the embedded seed-frame iframe.
- **Sheet** (`0 10px 30px -18px rgba(16,26,22,0.45)`): Receive, Send and New payment sheets that open inside the wallet.
- **Checkout card** (`0 12px 36px -22px rgba(16,26,22,0.5)`): the payment card and the coin chooser.
- **Hallmark inner rule** (`inset 0 0 0 1.5px <field>, inset 0 0 0 2.5px currentColor`): the punch's second line inside every mark. This is structure, not elevation.

**The Flat Rows Rule.** Cards, balances, tables and empty states carry no shadow. Elevation belongs only to things that open or are being chosen.

## Shapes

The corners form a small ladder: hallmarks at 3px (crisp, punched), the focus ring at 4px, inputs, notices and recovery words at 6px, the frame at 8px, grouped lists and cards at 14px (`--radius-lg`), the checkout card at 20px, and everything you press in the app layout (primary buttons, round actions, icon buttons, chips, segmented controls, the tab bar, the shop band, search) fully round. Borders are 1px hairlines. Hallmarks use a 1.5px rule doubled by the inset inner rule. Empty states use a 1px dashed `line-strong` border, and unstruck progress marks use a dashed rule. The brand emblem is a 22px rounded square of touchstone crossed by a diagonal gold stripe, drawn in CSS.

## Components

### Buttons
Solid and quiet: touchstone by default, tactile only with a 1px press.
- **Shape:** 6px corners, 44px minimum height, 8px by 16px padding, label weight 600.
- **Primary:** `action` fill and border with `action-text`. Hover goes to `action-hover`, active presses down 1px, and disabled is 45% opacity.
- **Secondary:** `surface` fill, `line-strong` border, ink text. Hover moves the border to ink and the fill to `surface-2`.
- **Link:** inline verdigris text with a 1px underline at 0.18em offset that thickens to 2px on hover.
- **Compact:** 36px high with 4px by 12px padding, used inside balance rows, the integration language tabs and the shell's Lock button. The shell button is transparent with an on-stone border.
- **Focus:** a 2px gold outline at a 2px offset (inset by 4px on tabs).

### Hallmarks (signature component)
Small struck cartouches that carry coin, network, status and sign-up step.
- **Style:** inline, 1px by 8px padding, 3px corners, a 1.5px `currentColor` rule with an inset inner rule, mark typography. The field is `--mark-field` (default `surface`, `surface-2` on hovered table rows).
- **States:** muted (neutral or unstruck), ink (choosing, pending, confirming; deliberately not gold), `ok` (confirmed), `bad` (expired or failed), and **proven** (gold-ink on gold-bg), used for confirmed payments and the current sign-up step.
- **Strike:** when a stage is first reached, the mark animates over 260ms with `cubic-bezier(0.16, 1, 0.3, 1)`, from scale 1.45 with blur and no opacity, settling through 0.96 to 1. This is disabled under `prefers-reduced-motion`. A mark is struck only on a real status change, never on first draw.
- **Checkout strip:** an ordered list of Waiting, Seen, Confirmed. Unstruck marks are dashed with no inner rule. The current stage (Waiting, then Seen) is gold-ink on gold-bg, the "you are here" use of gold, like the stepper's current step. Done marks are solid `ok`, and Confirmed, once reached, is proven gold. On expiry, the first mark reads Expired in `bad`.
- **Stepper:** the same marks numbered ("1 Write it down", "2 Check", "3 Password"). Done steps are `ok` and the current step is proven gold.

### Cards / Containers
- **Corner Style:** 8px.
- **Background:** `surface` on `ground`.
- **Shadow Strategy:** none at rest (see Elevation); sheets and the checkout card take their own shadows.
- **Border:** 1px `line`.
- **Internal Padding:** 24px, or 16px at 640px and below. Stacked cards are 16px apart. Sheets enter with a 220ms fade and a 4px drop.

### Inputs / Fields
- **Style:** `surface` fill, 1px `line-strong` border, 6px corners, 44px minimum height, 8px by 12px padding. Read-only fields use the `surface-2` fill and mono.
- **Hover:** the border moves to ink.
- **Focus:** the border goes gold with a 3px gold-bg halo and a 1px gold ring.
- **Error:** `aria-invalid` turns the border `bad`. Field errors sit beneath in `bad` at label size. Block errors are `bad` on `bad-bg`.
- **Select:** native appearance removed, with a chevron drawn from two CSS gradients in ink.
- **Copy row:** a field and a non-shrinking Copy button 8px apart, used for every address, amount and key.

### Navigation
- **Tab bar / sidebar:** see Layout. Tabs are buttons in a `tablist` (arrow keys, Home and End move between them). Muted `on-stone-muted` labels; hover and selected sit on `stone-2` in `on-stone`; the selected icon is gold, the "you are here" use of gold.
- **Top bar:** see Layout. Round 44px icon buttons on `surface` with a hairline.

### Grouped list (the main container)
- One `surface` card, 14px corners, 1px `line` border, rows divided by hairlines. Section titles sit above in muted label size.
- **Rows** are whole buttons (60px minimum, 68px for coins): leading mark, a main column (title at weight 650, muted sub line), an end column (amount, value or status), and a muted chevron. Hover and press tint the row `surface-2`.
- **Static rows** (label | value) for read-only facts; long values such as addresses take their own line under the label.

### Round actions
- 52px `action` circles with a 22px stroke icon, label under in label size at weight 600. Hover lifts 1px; keyboard focus rings the circle.

### Coin marks
- The coin's logo at 40px (64px on the coin screen), from `frontend/public/coins/`: colour SVGs from the *cryptocurrency-icons* set v0.18.1 (CC0-1.0, no attribution needed). Tokens get the chain's logo as an 18px badge at the lower right; test networks get a grey "t" badge. A coin without a logo falls back to a disc in its colour with its sign (₿, Ξ, Ł, Ð, B, T, ₮). To add a coin, put `<asset>.svg` (lower case) in `coins/` and add it to `COIN_LOGO` in `wallet.js` and `frontend/app/src/coins.tsx`.

### Chips and segmented controls
- **Chips** (status filters): pill buttons on `surface`; the pressed one is `action`. They scroll sideways on narrow screens.
- **Segmented** (chart range, code language): a pill track on `surface-2`; the pressed segment is `surface` with a small shadow.

### Chart
- One line in ink over a `surface-2` area, drawn as SVG from closing prices; the change over the range is coloured up or down; a shimmer while loading; "No price history right now." when the market source is down. Its text alternative states the range, first and last price, low and high.

### Notices and Empty States
- **Notices:** warnings are `warning-text` on `wait-bg`, success is `ok` on `ok-bg` with an `ok` border, and checkout status is centered at weight 600 on `surface-2`, or toned good or bad. All use 12px by 16px padding and 6px corners.
- **Empty state:** centered, 32px by 24px padding, dashed `line-strong` border, a title-small heading and a muted line capped at 52ch.

### Code Sample
- A touchstone field with on-stone mono at 0.79rem and line-height 1.6, 16px padding and 6px corners. It scrolls (440px maximum height) and keeps whitespace.

## Do's and Don'ts

### Do:
- **Do** take every color from the tokens. Each one has a light and dark value, so a new surface works in both schemes without its own override.
- **Do** carry coin, network and status in hallmarks, and use the same words everywhere: Choosing a coin, Waiting, Seen, Confirmed, Expired.
- **Do** strike a hallmark (the 260ms strike) only when its stage is actually reached, and respect reduced motion.
- **Do** keep every gap on the 4px armature and every size on the 1.125 scale.
- **Do** use mono for addresses, keys, phrases, secrets and code, and nowhere else.
- **Do** keep QR codes and shop logos on a white field with a hairline border in both schemes.
- **Do** keep element ids stable. The E2E suite (`tests/e2e/`) selects by id and visible text, and the screenshot goldens in `tests/e2e/goldens/` must be refreshed when a visual change is intended.
- **Do** rebuild the seed frame with `frontend/build_frame.py` after any change to `styles.css`. It is inlined into the sandboxed frame and pinned by SHA-256. Page-only styles go in `wallet.css` or `frontend/app/src/pay/pay.css`; `styles.css` changes also need `npm run build` in `frontend/app/`.

### Don't:
- **Don't** use gold for pending, in-flight or warning states. It means proven or "you are here".
- **Don't** load fonts, images or scripts from another origin, or add inline `style` or `<script>`. The CSP forbids them.
- **Don't** add a second type family or a display-size element on checkout besides the amount.
- **Don't** invert or tint QR codes.
- **Don't** put shadows on resting cards, rows or tables, and don't use hard offset shadows.
- **Don't** shrink balance or payment cells to fit; wrap the row instead.
