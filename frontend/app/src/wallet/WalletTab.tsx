// Wallet tab: home (total, actions, coins), coin picker, coin screen with chart, Receive, Send, New payment.
import { useEffect, useState } from "react";
import { CoinIcon, TESTNETS } from "../coins";
import { Qr } from "../qr";
import { api, type Balance } from "./api";
import { useWallet } from "./context";
import { PaymentRow } from "./PaymentsTab";
import {
  Act, Chevron, COIN_NAMES, CopyButton, CURRENCY, fiatOf, Icon, ICONS, isNative, key, label, LockIcon, money, pct,
  Screen, SENDABLE, TRON, trend,
} from "./ui";

type Kind = "send" | "receive";

export function WalletTab() {
  const [kind, setKind] = useState<Kind>("send"); // what the coin picker is for
  return (
    <>
      <Home onPick={setKind} />
      <Picker kind={kind} />
      <Coin />
      <Receive />
      <Send />
      <Charge />
    </>
  );
}

// --- Home -----------------------------------------------------------------------------------------
function Home({ onPick }: { onPick: (kind: Kind) => void }) {
  const w = useWallet();
  const [scan, setScan] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);
  let total = 0, start = 0, priced = false, incomplete = false;
  for (const b of w.balances) {
    const fiat = fiatOf(b, w.market), m = w.market[b.asset];
    if (fiat != null) {
      priced = true;
      total += fiat;
      start += m.change != null ? fiat / (1 + m.change) : fiat; // what it was worth 24 h ago
    }
    if (!b.complete) incomplete = true;
  }
  let change: string, tone = "";
  if (w.balancesLoaded && priced && start > 0 && total !== start) {
    const diff = total - start;
    change = `${diff >= 0 ? "+" : "−"}${money(Math.abs(diff))} (${pct(diff / start)}) today`;
    tone = trend(diff);
  } else {
    change = !w.balancesLoaded ? "" : priced ? "Total in " + CURRENCY : "Prices unavailable right now";
  }

  const findLate = async () => {
    setScanning(true);
    setScan("Checking every payment address for coins that arrived late. This can take a minute…");
    try {
      const r = await api<{ checked: number; found: number; failed: number }>("POST", "/wallet/scan");
      setScan(`Checked ${r.checked} address${r.checked === 1 ? "" : "es"}. ` +
        (r.found ? `Found coins on ${r.found}; they're now in your balances and payments.` : "No late coins found.") +
        (r.failed ? ` ${r.failed} couldn't be checked right now; try again later.` : ""));
      if (r.found) w.loadDashboard();
    } catch (e) {
      setScan(`Couldn't check the addresses (${(e as Error).message}).`);
    }
    setScanning(false);
  };
  const pick = (kind: Kind) => { onPick(kind); w.open("picker", "home"); };

  return (
    <Screen id="home">
      <header className="topbar">
        <button type="button" id="lock-top" className="icon-btn" aria-label="Lock" onClick={w.lock}><LockIcon /></button>
        <span className="pill"><span className="brand-mark" aria-hidden="true"></span><span id="wallet-name">{w.me?.shop_name || "Main wallet"}</span></span>
        <button type="button" id="refresh" className="icon-btn" aria-label="Refresh balances" onClick={w.loadDashboard}><Icon d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7" /></button>
      </header>

      <div className="hero">
        <h2 className="visually-hidden">Balance</h2>
        <p id="total" className="total">{!w.balancesLoaded ? "…" : priced ? money(total) : "—"}</p>
        <p id="total-change" className="change" data-trend={tone}>{change}</p>
      </div>

      <div className="quick" role="group" aria-label="Actions">
        <Act id="act-send" icon={<Icon d={ICONS.send} />} onClick={() => pick("send")}>Send</Act>
        <Act id="act-receive" icon={<Icon d={ICONS.receive} />} onClick={() => pick("receive")}>Receive</Act>
        <Act id="act-charge" icon={<Icon d={ICONS.add} />} onClick={() => w.showCharge(null, "home")}>New payment</Act>
        <Act id="scan" icon={<svg className="i" viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6.5" /><path d="M20 20l-4.4-4.4" /></svg>}
             onClick={findLate} disabled={scanning}>Find late</Act>
      </div>
      <p id="scan-result" className="note banner" role="status" hidden={scan == null}>{scan}</p>
      <p id="send-result" className="status-good" role="status" hidden={!w.sendResult}>{w.sendResult}</p>

      <ul id="balances" className="list coins" aria-label="Your coins" aria-live="polite">
        {!w.balancesLoaded
          ? <li className="note row-static">Checking the blockchains…</li>
          : w.balances.map((b) => <CoinRow key={key(b)} b={b} onClick={() => w.showCoin(b)} />)}
      </ul>
      <p id="balances-note" className="note" hidden={!incomplete}>Some networks couldn't be checked right now, so those balances may be higher. Try Refresh in a minute.</p>
    </Screen>
  );
}

function CoinRow({ b, onClick }: { b: Balance; onClick: () => void }) {
  const w = useWallet();
  const name = label(b.chain, b.asset), m = w.market[b.asset] || { price: null, change: null }, fiat = fiatOf(b, w.market);
  const native = isNative(b);
  return (
    <li>
      <button type="button" className="row coin-row" onClick={onClick}
              aria-label={`${name[0]} on ${name[1]}: ${b.complete ? "" : "at least "}${b.balance} ${b.asset}`}>
        <CoinIcon asset={b.asset} chain={b.chain} />
        <span className="row-main">
          <span className="row-title">
            <span>{native ? name[1] : name[0]}</span>{" "}
            <span className={native ? "network visually-hidden" : "network mark"}>{name[1]}</span>
          </span>
          <span className="row-sub">
            {TESTNETS[b.chain] ? "Test coins, no real value" : m.price != null && <>
              {money(m.price) + " "}
              {m.change != null && <span className="change" data-trend={trend(m.change)}>{pct(m.change)}</span>}
            </>}
          </span>
        </span>
        <span className="row-end">
          <span className={"amount" + (b.balance === "0" ? " zero" : "")}>{(b.complete ? "" : "≥ ") + b.balance + " " + b.asset}</span>
          <span className="row-sub">{fiat == null || b.balance === "0" ? "" : money(fiat)}</span>
        </span>
        <Chevron />
      </button>
    </li>
  );
}

// --- Send / Receive from the home actions: choose the coin first ------------------------------------
function Picker({ kind }: { kind: Kind }) {
  const w = useWallet();
  const send = kind === "send";
  return (
    <Screen id="picker" title={send ? "Send which coin?" : "Receive which coin?"} titleId="picker-title" back>
      <ul id="picker-list" className="list coins">
        {w.isOpen("picker") && w.balances.filter((b) => !send || SENDABLE.includes(key(b))).map((b) => (
          <CoinRow key={key(b)} b={b} onClick={() => (send ? w.showSend(b, "picker") : w.showReceive(b, "picker"))} />
        ))}
      </ul>
    </Screen>
  );
}

// --- One coin -------------------------------------------------------------------------------------------
function Coin() {
  const w = useWallet();
  const b = w.coin;
  const name = b ? label(b.chain, b.asset) : ["", ""];
  const m = (b && w.market[b.asset]) || { price: null, change: null };
  const fiat = b ? fiatOf(b, w.market) : null;
  const mine = b ? w.payments.filter((p) => p.chain === b.chain && p.asset === b.asset).slice(0, 10) : [];
  return (
    <Screen id="coin" title={b ? (isNative(b) ? COIN_NAMES[b.asset] : name[0] + " · " + name[1]) : ""} titleId="coin-title" back>
      {b && <>
        <div className="hero">
          <CoinIcon id="coin-icon" asset={b.asset} chain={b.chain} large />
          <p id="coin-balance" className="total">{(b.complete ? "" : "≥ ") + b.balance + " " + b.asset}</p>
          <p id="coin-fiat" className="change">{TESTNETS[b.chain] ? "Test coins, no real value" : fiat == null ? "" : money(fiat)}</p>
        </div>
        <div className="quick" role="group" aria-label="Coin actions">
          <Act id="coin-send" icon={<Icon d={ICONS.send} />} onClick={() => w.showSend(b, "coin")} hidden={!SENDABLE.includes(key(b))}>Send</Act>
          <Act id="coin-receive" icon={<Icon d={ICONS.receive} />} onClick={() => w.showReceive(b, "coin")}>Receive</Act>
          <Act id="coin-charge" icon={<Icon d={ICONS.add} />} onClick={() => w.showCharge(b, "coin")}>New payment</Act>
        </div>

        <Chart b={b} />

        <ul className="list">
          <li className="row-static"><span>Price</span><span id="coin-price">{m.price == null ? "Unavailable" : money(m.price) + (m.change != null ? ` (${pct(m.change)})` : "")}</span></li>
          <li className="row-static"><span>Network</span><span id="coin-network">{name[1]}</span></li>
          <li className="row-static address-row"><span>Main address</span><span id="coin-address" className="mono">{b.main_address}</span></li>
        </ul>

        <h3 className="group-title">Payments</h3>
        <ul id="coin-payments" className="list" hidden={!mine.length}>
          {mine.map((p) => <PaymentRow key={p.id} p={p} from="coin" />)}
        </ul>
        <p id="coin-payments-empty" className="note" hidden={!!mine.length}>No payments in this coin yet.</p>
      </>}
    </Screen>
  );
}

// The chart: closing prices as one line, drawn as SVG (no chart library).
const SPANS = ["1H", "1D", "1W", "1M", "1Y"];
const W = 600, H = 180, PAD = 8;

function Chart({ b }: { b: Balance }) {
  const w = useWallet();
  const [span, setSpan] = useState("1D");
  const [points, setPoints] = useState<number[] | null>(null); // null = loading
  const [n, setN] = useState(0); // re-fetch each time the coin screen opens
  const opened = w.isOpen("coin");
  useEffect(() => { if (opened) setN((x) => x + 1); }, [opened, b.asset]);
  useEffect(() => {
    if (!n) return;
    let gone = false;
    setPoints(null);
    api<{ points: [number, string][] }>("GET", `/market/${b.asset}/history?span=${span}&currency=${CURRENCY}`)
      .then((r) => r.points.map((p) => Number(p[1])), () => [])
      .then((v) => { if (!gone) setPoints(v); }); // the user may have moved on
    return () => { gone = true; };
  }, [n, span, b.asset]);

  const price = w.market[b.asset]?.price;
  let body: React.ReactNode = null, aria = "Price chart", change: number | null = null;
  if (points && points.length >= 2) {
    const low = Math.min(...points), high = Math.max(...points), range = high - low || 1;
    const line = points.map((v, i) => `${i ? "L" : "M"}${((i / (points.length - 1)) * W).toFixed(1)} ${(PAD + (1 - (v - low) / range) * (H - 2 * PAD)).toFixed(1)}`).join("");
    const first = points[0], last = points[points.length - 1];
    change = (last - first) / first;
    aria = `${b.asset} price over ${span}: from ${money(first)} to ${money(last)}, low ${money(low)}, high ${money(high)}`;
    body = (
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
        <path d={`${line}L${W} ${H}L0 ${H}Z`} className="chart-area" />
        <path d={line} className="chart-line" />
      </svg>
    );
  } else if (points) {
    aria = "Price chart unavailable";
    body = <p className="note center">No price history right now.</p>;
  }
  return (
    <section className="chart-card" aria-labelledby="chart-title">
      <h3 id="chart-title" className="visually-hidden">Price chart</h3>
      <p className="chart-head"><strong id="chart-price">{price == null ? "" : money(price)}</strong>{" "}
        <span id="chart-change" className="change" data-trend={trend(change)}>{change == null ? "" : pct(change)}</span></p>
      <div id="chart" className={"chart" + (points ? "" : " loading-chart")} role="img" aria-label={aria}>{body}</div>
      <div id="chart-spans" className="segmented" role="group" aria-label="Chart range">
        {SPANS.map((s) => <button key={s} type="button" data-span={s} aria-pressed={s === span} onClick={() => setSpan(s)}>{s}</button>)}
      </div>
    </section>
  );
}

// --- Receive -----------------------------------------------------------------------------------------
function Receive() {
  const w = useWallet();
  const b = w.receiving;
  const name = b ? label(b.chain, b.asset) : ["", ""];
  return (
    <Screen id="receive" as="section" title={"Receive " + name[0]} titleId="receive-title" back backId="receive-close">
      <div className="sheet-body">
        <p className="center">Your main address on <strong id="receive-network">{name[1]}</strong>. Also where gas for sending goes.</p>
        <Qr id="receive-qr" size={220} text={b?.main_address || ""} />
        <div className="copy-row">
          <textarea id="receive-address" rows={2} readOnly spellCheck={false} aria-label="Your main address" value={b?.main_address || ""} />
          <CopyButton id="receive-copy" target="receive-address" />
        </div>
        <p id="receive-warning" className="warning">{b ? `Send only ${name[0]} on ${name[1]} to this address. Other coins, tokens or networks can be lost.` : ""}</p>
      </div>
    </Screen>
  );
}

// --- Send ----------------------------------------------------------------------------------------------
function Send() {
  const w = useWallet();
  const s = w.sending;
  // A fresh form each time Send opens (key), with the coin it opened for.
  return s ? <SendForm key={s.n} b={s.b} /> : <Screen id="send" as="section" title="Send" titleId="send-title" back backId="send-close"><div className="sheet-body" /></Screen>;
}

function SendForm({ b }: { b: Balance }) {
  const w = useWallet();
  const [amount, setAmount] = useState("");
  const [to, setTo] = useState("");
  const [max, setMax] = useState(false);
  const [speed, setSpeed] = useState("normal");
  const m = w.market[b.asset], n = Number(amount);
  const fiat = max ? "Your whole balance, minus the fee"
    : !TESTNETS[b.chain] && m && m.price != null && n > 0 ? "≈ " + money(n * m.price) : "";

  const review = async () => {
    const dest = to.trim(), value = amount.trim();
    const error = !dest ? "Enter the address to send to." : (!max && !/^\d+(\.\d+)?$/.test(value) ? "Enter an amount, like 0.005." : "");
    w.setSendError(error);
    if (error) return;
    w.setReviewing(true);
    let prep: any;
    try {
      prep = await api("GET", `/wallet/send/${b.chain}?asset=${encodeURIComponent(b.asset)}&to=${encodeURIComponent(dest)}`);
    } catch (err) {
      w.setSendError((err as Error).message);
      w.setReviewing(false);
      return;
    }
    w.showSigner();
    const request: Record<string, unknown> = { id: String(Date.now()), chain: b.chain, asset: b.asset, to: dest, amount: value, max };
    if (prep.kind === "evm") {
      Object.assign(request, { fee: prep.fees[speed], gas_limit: prep.gas_limit, sources: prep.sources, main: prep.main });
    } else if (prep.kind === "tron") {
      Object.assign(request, { params: prep.params, block: prep.block, to_active: prep.to_active, sources: prep.sources, main: prep.main });
    } else {
      Object.assign(request, { fee_per_byte: prep.fee_per_byte[speed], utxos: prep.utxos, change_index: prep.change_index });
    }
    w.post({ type: "sign-send", request });
  };

  return (
    <Screen id="send" as="section" title={"Send " + b.asset} titleId="send-title" back backId="send-close">
      <div className="sheet-body">
        <label htmlFor="send-amount" className="visually-hidden">Amount</label>
        <div className="amount-entry">
          <input id="send-amount" inputMode="decimal" autoComplete="off" placeholder="0" value={amount} disabled={max} onChange={(e) => setAmount(e.target.value)} />
          <span id="send-unit" className="unit">{b.asset}</span>
        </div>
        <p id="send-fiat" className="center note">{fiat}</p>
        <p className="center"><label className="check pill-check">
          <input type="checkbox" id="send-max" checked={max} onChange={(e) => { setMax(e.target.checked); if (e.target.checked) setAmount(""); }} /> Max
        </label></p>
        <ul className="list form-list">
          <li>
            <label htmlFor="send-to">To</label>
            <input id="send-to" autoComplete="off" spellCheck={false} autoCapitalize="off" placeholder="Address" value={to} onChange={(e) => setTo(e.target.value)} />
          </li>
          <li className="row-static"><span>Network</span><strong id="send-network">{label(b.chain, b.asset)[1]}</strong></li>
          <li id="send-speed-box" hidden={!!TRON[b.chain]}>{/* Tron fees are fixed by the network */}
            <label htmlFor="send-speed">Network fee</label>
            <select id="send-speed" value={speed} onChange={(e) => setSpeed(e.target.value)}>
              <option value="slow">Slow (cheapest)</option>
              <option value="normal">Normal</option>
              <option value="fast">Fast</option>
            </select>
          </li>
        </ul>
        <p className="note">From your whole balance. With Max, the fee comes out of it. You'll check everything in the secure window before anything is signed.</p>
        <p className="error" id="send-error" role="alert">{w.sendError}</p>
        <button type="button" id="send-review" className="wide" disabled={w.reviewing} onClick={review}>Review in the secure window</button>
      </div>
    </Screen>
  );
}

// --- New payment: what a shop does with POST /payments, here with the wallet session ------------------
function Charge() {
  const w = useWallet();
  const [amount, setAmount] = useState("");
  const [unit, setUnit] = useState("coin");
  const [net, setNet] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<React.ReactNode>(null);
  const options = w.balances.map(key);
  const chosen = options.includes(net) ? net : (!net && options.includes("SEPOLIA/ETH") ? "SEPOLIA/ETH" : options[0] || "");

  useEffect(() => { // opened from a coin: that coin, priced in it
    setError("");
    if (w.charge.b) { setUnit("coin"); setNet(key(w.charge.b)); }
  }, [w.charge.n]); // eslint-disable-line react-hooks/exhaustive-deps

  const create = async () => {
    const [chain, asset] = chosen.split("/");
    setError("");
    setResult(null);
    setBusy(true);
    try {
      const p = await api<{ id: string; chain?: string; asset: string; amount: string; address: string; price?: string; currency?: string }>(
        "POST", "/payments", unit === "coin" ? { chain, asset, amount: amount.trim() } : { price: amount.trim(), currency: unit });
      setResult(<>
        {"Payment created: " + (p.chain ? `${p.amount} ${p.asset} to ${p.address}` : `${p.price} ${p.currency}, the payer picks the coin`) + ". "}
        <a href={"/pay/" + encodeURIComponent(p.id)} target="_blank" rel="noopener">Open the checkout page</a>
      </>);
      w.loadPayments();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const priced = unit !== "coin";
  return (
    <Screen id="charge" as="section" title="New payment" titleId="charge-title" back>
      <div className="sheet-body">
        <label htmlFor="charge-amount" className="visually-hidden">Amount</label>
        <div className="amount-entry">
          <input id="charge-amount" inputMode="decimal" autoComplete="off" placeholder="0.00" value={amount} onChange={(e) => setAmount(e.target.value)} />
          <select id="charge-unit" aria-label="Amount in" value={unit} onChange={(e) => setUnit(e.target.value)}>
            <option value="coin">Coin</option>
            <option value="USD">USD</option>
            <option value="EUR">EUR</option>
          </select>
        </div>
        <ul className="list form-list">
          <li id="charge-network-box" hidden={priced}>
            <label htmlFor="charge-network">Coin and network</label>
            <select id="charge-network" value={chosen} onChange={(e) => setNet(e.target.value)}>
              {w.balances.map((b) => { const name = label(b.chain, b.asset); return <option key={key(b)} value={key(b)}>{name[0] + " on " + name[1]}</option>; })}
            </select>
          </li>
        </ul>
        <p id="charge-priced-note" className="note" hidden={!priced}>The payer picks any coin you can receive. The amount is converted at the current rate when they choose.</p>
        <p className="note">Makes a payment request and its checkout page, like your shop does with the API. Each one gets a fresh address.</p>
        <p className="error" id="charge-error" role="alert">{error}</p>
        <button type="button" id="charge-create" className="wide" disabled={busy} onClick={create}>Create checkout</button>
        <p id="charge-result" className="status-good" role="status" hidden={!result}>{result}</p>
      </div>
    </Screen>
  );
}
