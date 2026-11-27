// Payments tab: grouped by day, newest first, with search and status chips; one payment's details.
import { Fragment, useState } from "react";
import { CoinIcon } from "../coins";
import type { Payment } from "./api";
import { useWallet } from "./context";
import { Chevron, label, Screen } from "./ui";

const STATUS: Record<string, string> = { choosing: "Choosing a coin", pending: "Waiting", confirming: "Seen", confirmed: "Confirmed", expired: "Expired" }; // same words as the checkout
const CHIPS = [["", "All"], ["choosing", "Choosing a coin"], ["pending", "Waiting"], ["confirming", "Seen"], ["confirmed", "Confirmed"], ["expired", "Expired"]];

const received = (p: Payment) => !!p.amount_received && p.amount_received !== "0"; // price requests have none yet
const amountText = (p: Payment) => (p.chain ? p.amount + " " + label(p.chain, p.asset)[0] : p.price + " " + p.currency);
const timeOf = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

function dayLabel(iso: string) {
  const d = new Date(iso), today = new Date(), y = new Date();
  y.setDate(today.getDate() - 1);
  if (d.toDateString() === today.toDateString()) return "Today";
  if (d.toDateString() === y.toDateString()) return "Yesterday";
  return d.toLocaleDateString([], { day: "numeric", month: "short", year: d.getFullYear() === today.getFullYear() ? undefined : "numeric" });
}

function matches(p: Payment, query: string, status: string) {
  if (status && p.status !== status) return false;
  if (!query) return true;
  return [p.id, p.reference, p.address, p.amount, p.price, p.asset, p.chain].some((v) => v && String(v).toLowerCase().includes(query));
}

// A status hallmark; "struck" when it changed since the previous load.
function StatusMark({ p, strike }: { p: Payment; strike?: boolean }) {
  const cls = ["mark"];
  if (p.status === "confirmed") cls.push("proven");
  if (strike) cls.push("struck");
  let text = STATUS[p.status] || p.status;
  if (p.status === "expired" && received(p)) text += ", " + p.amount_received + " received";
  return <span className={cls.join(" ")} data-state={p.status}>{text}</span>;
}

export function PaymentRow({ p, from }: { p: Payment; from: string }) {
  const w = useWallet();
  return (
    <li data-id={p.id}>
      <button type="button" className="row pay-row" onClick={() => w.showPayment(p, from)}>
        {p.chain ? <CoinIcon asset={p.asset} chain={p.chain} /> : <span className="coin coin-any">$</span>}
        <span className="row-main">
          <span className="row-title"><span>{amountText(p)}</span>{p.chain && <>{" "}<span className="mark">{label(p.chain, p.asset)[1]}</span></>}</span>
          <span className="row-sub">{(p.reference ? p.reference + " · " : "") + (p.chain ? "" : "Payer picks the coin · ") + timeOf(p.created_at)}</span>
        </span>
        <span className="row-end">
          <StatusMark p={p} strike={from === "payments-home" && w.struck.has(p.id)} />
          <span className="row-sub">{p.price && p.chain ? p.price + " " + p.currency : ""}</span>
        </span>
        <Chevron />
      </button>
    </li>
  );
}

export function PaymentsTab() {
  return (
    <>
      <PaymentsHome />
      <PaymentDetail />
    </>
  );
}

function PaymentsHome() {
  const w = useWallet();
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const q = query.trim().toLowerCase();
  const shown = w.payments.filter((p) => matches(p, q, status));
  const days: [string, Payment[]][] = [];
  for (const p of shown) {
    const d = dayLabel(p.created_at);
    if (!days.length || days[days.length - 1][0] !== d) days.push([d, []]);
    days[days.length - 1][1].push(p);
  }
  return (
    <Screen id="payments-home" title="Payments">
      <label htmlFor="pay-search" className="visually-hidden">Search payments</label>
      <input id="pay-search" className="search" type="search" placeholder="Search id, reference, address or amount" autoComplete="off" spellCheck={false}
             value={query} onChange={(e) => setQuery(e.target.value)} />
      <div id="pay-filter" className="chips" role="group" aria-label="Status">
        {CHIPS.map(([s, name]) => <button key={s} type="button" data-status={s} aria-pressed={status === s} onClick={() => setStatus(s)}>{name}</button>)}
      </div>
      <div id="payments-empty" className="empty" hidden={!!w.payments.length}>
        <h3>No payments yet</h3>
        <p>Payments your shop creates through the API show up here, and so do the ones you make with <strong>New payment</strong> in the wallet.</p>
      </div>
      <p id="payments-nomatch" className="note" hidden={!w.payments.length || !!shown.length}>No payments match. Clear the search or pick another status.</p>
      <div id="payments" hidden={!shown.length}>
        {days.map(([day, list]) => (
          <Fragment key={day}>
            <h3 className="group-title">{day}</h3>
            <ul className="list">{list.map((p) => <PaymentRow key={p.id} p={p} from="payments-home" />)}</ul>
          </Fragment>
        ))}
      </div>
    </Screen>
  );
}

// One payment, as a grouped list.
function PaymentDetail() {
  const w = useWallet();
  const p = w.payment;
  const fields: [string, string | undefined | null, boolean?][] = !p ? [] : [
    ["Network", p.chain && label(p.chain, p.asset)[1]],
    ["Price", p.price && p.price + " " + p.currency + (p.rate ? ` (1 ${p.asset} = ${p.rate} ${p.currency})` : "")],
    ["Received", p.chain && p.amount_received + " " + p.asset],
    ["Reference", p.reference],
    ["Address", p.address, true],
    ["Created", new Date(p.created_at).toLocaleString()],
    ["Expires", p.expires_at && new Date(p.expires_at).toLocaleString()],
    ["Payment id", p.id, true],
  ];
  const explorer = p && received(p) ? (p.transactions && p.transactions.length === 1 ? p.transactions[0].url : p.address_url) : undefined;
  return (
    <Screen id="payment-detail" title="Payment" back>
      <div className="hero">
        <p id="pd-amount" className="total">{p ? amountText(p) : ""}</p>
        <p id="pd-status">{p && <StatusMark p={p} />}</p>
      </div>
      <ul id="pd-fields" className="list">
        {fields.filter(([, v]) => v !== undefined && v !== null && v !== "").map(([name, value, mono]) => (
          <li key={name} className="row-static"><span>{name}</span><span className={mono ? "mono" : ""}>{value}</span></li>
        ))}
      </ul>
      <div className="actions center">
        <a id="pd-checkout" className="button-link" target="_blank" rel="noopener" href={p ? "/pay/" + encodeURIComponent(p.id) : undefined}>Open checkout page</a>
        <a id="pd-explorer" className="button-link secondary" target="_blank" rel="noopener noreferrer" hidden={!explorer} href={explorer}>View on the block explorer</a>
      </div>
    </Screen>
  );
}
