// Checkout page: shows one payment and follows its status. Reads GET /pay/{id}/status, writes only
// POST /pay/{id}/choose. Docs: docs/api.md#checkout-page
import { useEffect, useRef, useState } from "react";
import { CoinIcon, money, networkName } from "../coins";

type Option = { chain: string; asset: string; amount: string; available: boolean };
type Payment = {
  id: string;
  status: "choosing" | "pending" | "confirming" | "confirmed" | "expired";
  chain?: string;
  asset: string;
  amount: string;
  amount_due: string;
  amount_received: string;
  address: string;
  address_url: string;
  confirmations_required: number;
  expires_at: string;
  server_time: string;
  price?: string;
  currency?: string;
  options: Option[];
  shop_name?: string | null;
  logo_url?: string | null;
  return_url?: string | null;
};

const POLL_MS = 5000;
// BIP21 URIs carry the amount, and every BTC/LTC/DOGE wallet reads them. For EVM and Tron the QR
// is the bare address: payment URIs there are read inconsistently and could send the wrong token.
const URI_SCHEME: Record<string, string> = { BTC: "bitcoin", LTC: "litecoin", DOGE: "dogecoin" };
const STAGES: Record<string, number> = { pending: 0, confirming: 1, confirmed: 2 };
const STEPS = [["waiting", "Waiting"], ["seen", "Seen"], ["confirmed", "Confirmed"]];

const isOpen = (p: Payment) => p.status === "pending" || p.status === "confirming";
// A payment priced in USD/EUR waits for the payer to pick a coin; keep polling unless it expired.
const keepPolling = (p: Payment) => (p.chain ? isOpen(p) : p.status !== "expired");

export function Checkout({ id }: { id: string }) {
  const [payment, setPayment] = useState<Payment | null>(null);
  const [notFound, setNotFound] = useState(!id);
  const [offline, setOffline] = useState(false);
  const [, setNow] = useState(0);
  const clockOffset = useRef(0); // server time minus local time, so a wrong local clock can't fake the countdown

  useEffect(() => {
    if (!id) return;
    let timer = 0, stopped = false;
    const load = async () => {
      try {
        const r = await fetch(`/pay/${encodeURIComponent(id)}/status`, { cache: "no-store" });
        if (r.status === 404) return setNotFound(true);
        if (!r.ok) throw new Error("http " + r.status);
        const p: Payment = await r.json();
        clockOffset.current = Date.parse(p.server_time) - Date.now();
        setPayment(p);
        setOffline(false);
        if (!keepPolling(p)) return;
      } catch {
        setOffline(true); // network blip: keep trying, the payment is unaffected
      }
      if (!stopped) timer = window.setTimeout(load, POLL_MS);
    };
    load();
    return () => { stopped = true; clearTimeout(timer); };
  }, [id]);

  // Countdown between polls.
  const ticking = !!payment && isOpen(payment);
  useEffect(() => {
    if (!ticking) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [ticking]);

  if (notFound) {
    return <main className="pay"><p id="not-found" className="error" role="alert">This payment doesn't exist. Check the link, or ask the merchant for a new one.</p></main>;
  }
  if (!payment) {
    return <main className="pay"><p id="loading" className="loading-card" role="status">Loading payment…</p></main>;
  }
  const secondsLeft = Math.floor((Date.parse(payment.expires_at) - (Date.now() + clockOffset.current)) / 1000);
  return (
    <main className="pay">
      <Shop p={payment} />
      {payment.chain
        ? <PaymentBox p={payment} secondsLeft={secondsLeft} offline={offline} />
        : <Choose p={payment} id={id} onChosen={setPayment} />}
    </main>
  );
}

function Shop({ p }: { p: Payment }) {
  return (
    <div id="shop" className="shop" hidden={!(p.shop_name || p.logo_url)}>
      <img id="shop-logo" alt="" src={p.logo_url || undefined} hidden={!p.logo_url} />
      <span>Payment to <strong id="shop-name">{p.shop_name || "the merchant"}</strong></span>
    </div>
  );
}

function Choose({ p, id, onChosen }: { p: Payment; id: string; onChosen: (p: Payment) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const expired = p.status === "expired";

  const choose = async (o: Option) => {
    setError("");
    setBusy(true);
    try {
      const r = await fetch(`/pay/${encodeURIComponent(id)}/choose`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chain: o.chain, asset: o.asset }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error((data.detail && data.detail.error) || "HTTP " + r.status);
      onChosen(data);
    } catch (err) {
      setError(String((err as Error).message || err));
      setBusy(false);
    }
  };

  return (
    <section id="choose">
      <header>
        <p className="lede">Amount to pay</p>
        <p className="amount" id="price">{money(p.price!, p.currency!)}</p>
      </header>
      <h2 id="choose-title" hidden={expired}>Choose how to pay</h2>
      <div id="options" className="pay-options" role="group" aria-labelledby="choose-title">
        {!expired && p.options.map((o) => (
          <button key={o.chain + "/" + o.asset} type="button" className="row" disabled={busy || !o.available} onClick={() => choose(o)}>
            <CoinIcon asset={o.asset} />
            <span className="row-main">
              <span className="row-title">{o.asset} on <span className="mark">{networkName(o.chain, o.asset)}</span></span>
            </span>
            <span className="row-end">{o.available ? o.amount + " " + o.asset : "price unavailable"}</span>
          </button>
        ))}
      </div>
      <p className="error" id="choose-error" role="alert">
        {expired ? "This payment expired before a coin was chosen. Ask the merchant for a new one." : error}
      </p>
      <p className="note" id="choose-note" hidden={expired}>The amount is fixed when you choose. You then have 30 minutes to send it.</p>
    </section>
  );
}

function statusLine(p: Payment, secondsLeft: number): [string, string] {
  const asset = p.asset, received = p.amount_received !== "0";
  if (p.status === "confirmed") return ["Paid. Thank you.", "good"];
  if (p.status === "confirming") {
    return [`Payment received. Waiting for ${p.confirmations_required} network confirmations. You can close this page.`, "good"];
  }
  if (p.status === "expired") {
    return [received
      ? `This payment expired with ${p.amount_received} ${asset} of ${p.amount} received. Contact the merchant about it.`
      : "This payment expired. Don't send anything to this address. Ask the merchant for a new payment.", "bad"];
  }
  if (secondsLeft <= 0) return ["Time is up. Checking the network for your payment…", ""];
  if (received) {
    return [`Received ${p.amount_received} of ${p.amount} ${asset}. Send the remaining ${p.amount_due} ${asset} to the same address.`, ""];
  }
  return ["Waiting for your payment…", ""];
}

function PaymentBox({ p, secondsLeft, offline }: { p: Payment; secondsLeft: number; offline: boolean }) {
  const label = networkName(p.chain!, p.asset);
  const sending = p.status === "pending" && secondsLeft > 0; // only offer the address while sending is still useful
  const [text, tone] = offline ? ["Connection problem. Retrying…", ""] : statusLine(p, secondsLeft);
  let heading = sending ? "Send exactly" : "Payment of";
  if (p.price) heading += ` (${money(p.price, p.currency!)})`;
  const m = Math.floor(secondsLeft / 60), s = secondsLeft % 60;

  return (
    <section id="payment">
      <header className="due">
        <p className="due-label" id="heading">{heading}</p>
        <p className="amount"><span id="amount">{p.amount}</span> <span id="asset" className="unit">{p.asset}</span></p>
        <p className="network">on <strong id="network" className="mark">{label}</strong></p>
      </header>

      <Progress status={p.status} />

      <p id="status" className="status" role="status" aria-live="polite" data-tone={tone}>{text}</p>
      {/* The address on the block explorer lists the payment. No hashes on this page. */}
      <p id="tx-link" className="note" hidden={p.amount_received === "0"}>
        <a target="_blank" rel="noopener noreferrer" href={p.address_url}>View on the block explorer</a>
      </p>

      <div id="pay-box" hidden={!sending}>
        {sending && <Qr text={URI_SCHEME[p.chain!] ? `${URI_SCHEME[p.chain!]}:${p.address}?amount=${p.amount_due}` : p.address} />}
        <ul className="list pay-fields">
          <li>
            <label htmlFor="address">Address</label>
            <div className="copy-row">
              {/* Wraps instead of scrolling, so the payer can check the whole address */}
              <textarea id="address" rows={2} readOnly spellCheck={false} value={p.address} />
              <CopyButton target="address" value={p.address} />
            </div>
          </li>
          <li>
            <label htmlFor="amount-due">Amount</label>
            <div className="copy-row">
              <input id="amount-due" readOnly value={p.amount_due} />
              <CopyButton target="amount-due" value={p.amount_due} />
            </div>
          </li>
          <li className="row-static countdown"><span>Time left</span><strong id="countdown">{sending ? `${m}:${s < 10 ? "0" : ""}${s}` : "--:--"}</strong></li>
        </ul>
        <p id="network-warning" className="warning">Send only {p.asset} on {label} to this address. Other coins, tokens or networks can be lost.</p>
      </div>

      {/* The way back to the shop, once there's nothing left to do here. */}
      <p id="back" className="back" hidden={!p.return_url || p.status === "pending"}>
        <a id="back-link" href={p.return_url || undefined}>{p.return_url ? "Back to " + (p.shop_name || "the shop") : ""}</a>
      </p>
      <p className="note">Payment <code id="payment-id">{p.id}</code></p>
    </section>
  );
}

// The hallmark strip: Waiting -> Seen -> Confirmed. A mark reached since the previous render is
// "struck" (only live changes, not on first load).
function Progress({ status }: { status: Payment["status"] }) {
  const last = useRef(-1);
  const stage = status === "expired" ? -1 : STAGES[status];
  const prev = last.current;
  useEffect(() => { last.current = stage; });
  return (
    <ol id="progress" className="progress" aria-label="Payment progress">
      {STEPS.map(([step, name], i) => {
        if (status === "expired") {
          return <li key={step} className={i === 0 ? "mark bad" : "mark"} data-step={step}>{i === 0 ? "Expired" : name}</li>;
        }
        const cls = ["mark"];
        if (i < stage || (i === 2 && stage === 2)) cls.push("done");
        else if (i === stage) cls.push("now");
        if (i === 2 && stage === 2) cls.push("proven");
        if (prev >= 0 && i > prev && i <= stage) cls.push("struck");
        return <li key={step} className={cls.join(" ")} data-step={step}>{name}</li>;
      })}
    </ol>
  );
}

function Qr({ text }: { text: string }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    box.current!.replaceChildren(kjua({ text, render: "canvas", size: 240, ecLevel: "M", fill: "#000", back: "#fff" }));
  }, [text]);
  return <div id="qr" className="qr" aria-hidden="true" ref={box} />;
}

function CopyButton({ target, value }: { target: string; value: string }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    const select = () => (document.getElementById(target) as HTMLInputElement).select();
    if (!navigator.clipboard) return select();
    navigator.clipboard.writeText(value).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }, select);
  };
  return <button type="button" data-copy={target} className="secondary" onClick={copy}>{copied ? "Copied" : "Copy"}</button>;
}
