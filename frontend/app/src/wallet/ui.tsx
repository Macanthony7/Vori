// Wallet building blocks: names, money, icons, screens.
import { useState } from "react";
import { money as fmt, TESTNETS } from "../coins";
import { useWallet } from "./context";
import type { Balance, MarketEntry } from "./api";

export const CURRENCY = "USD";
export const NETWORKS: Record<string, [string, string]> = { // [what it is, which network]
  "BTC/BTC": ["BTC", "Bitcoin"],
  "LTC/LTC": ["LTC", "Litecoin"],
  "DOGE/DOGE": ["DOGE", "Dogecoin"],
  "ETH/ETH": ["ETH", "Ethereum"],
  "ETH/USDT": ["USDT", "Ethereum (ERC-20)"],
  "BSC/BNB": ["BNB", "BNB Smart Chain (BEP-20)"],
  "BSC/USDT": ["USDT", "BNB Smart Chain (BEP-20)"],
  "TRX/TRX": ["TRX", "Tron"],
  "TRX/USDT": ["USDT", "Tron (TRC-20)"],
  "SEPOLIA/ETH": ["ETH", "Sepolia testnet (no real value)"],
  "SEPOLIA/USDT": ["USDT", "Sepolia testnet (ERC-20, no real value)"],
  "NILE/TRX": ["TRX", "Tron Nile testnet (no real value)"],
  "NILE/USDT": ["USDT", "Tron Nile testnet (TRC-20, no real value)"],
};
export const COIN_NAMES: Record<string, string> = { BTC: "Bitcoin", LTC: "Litecoin", DOGE: "Dogecoin", ETH: "Ethereum", BNB: "BNB", TRX: "TRON", USDT: "Tether" };
// What can be sent so far (docs/roadmap.md), as "CHAIN/ASSET".
export const SENDABLE = ["BTC/BTC", "LTC/LTC", "DOGE/DOGE", "ETH/ETH", "ETH/USDT", "BSC/BNB", "BSC/USDT", "TRX/TRX", "TRX/USDT",
  "SEPOLIA/ETH", "SEPOLIA/USDT", "NILE/TRX", "NILE/USDT"];
export const TRON: Record<string, boolean> = { TRX: true, NILE: true };
export { TESTNETS };

export const label = (chain: string, asset: string): [string, string] => NETWORKS[chain + "/" + asset] || [asset, chain];
export const key = (b: { chain?: string; asset: string }) => b.chain + "/" + b.asset;
export const money = (value: number) => fmt(value, CURRENCY);
export const pct = (fraction: number) => { const n = fraction * 100; return (n >= 0 ? "+" : "") + n.toFixed(2) + "%"; };
export const trend = (fraction: number | null | undefined) => (fraction == null ? "" : fraction >= 0 ? "up" : "down"); // data-trend

// The chain's own coin on a real network (Bitcoin on Bitcoin): its name is the network's name.
export const isNative = (b: Balance) =>
  (!TESTNETS[b.chain] && label(b.chain, b.asset)[1] === COIN_NAMES[b.asset]) || (b.chain === "TRX" && b.asset === "TRX");

// Value of a balance in CURRENCY, or null (unknown price, or a test network).
export function fiatOf(b: Balance, market: Record<string, MarketEntry>): number | null {
  const m = market[b.asset];
  if (TESTNETS[b.chain] || !m || m.price == null) return null;
  return Number(b.balance) * m.price;
}

export function Icon({ d, className }: { d: string; className?: string }) {
  return <svg className={"i" + (className ? " " + className : "")} viewBox="0 0 24 24" aria-hidden="true"><path d={d} /></svg>;
}
export const Chevron = () => <Icon d="M9 6l6 6-6 6" className="chev" />;
export const ICONS = {
  send: "M12 19V5M5 12l7-7 7 7",
  receive: "M12 5v14M19 12l-7 7-7-7",
  add: "M12 5v14M5 12h14",
  back: "M15 18l-6-6 6-6",
};
export function LockIcon() {
  return <svg className="i" viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="11" width="14" height="9" rx="2" /><path d="M8 11V8a4 4 0 0 1 8 0v3" /></svg>;
}

// A round action button (Send, Receive, …).
export function Act({ id, icon, children, onClick, hidden, disabled }: {
  id: string; icon: React.ReactNode; children: React.ReactNode; onClick: () => void; hidden?: boolean; disabled?: boolean;
}) {
  return <button type="button" id={id} className="act" onClick={onClick} hidden={hidden} disabled={disabled}><span className="act-icon">{icon}</span>{children}</button>;
}

// One screen of a tab: only the panel's current screen shows. `back` adds the Back button.
export function Screen({ id, as = "div", title, titleId, back, backId, children }: {
  id: string; as?: "div" | "section"; title?: React.ReactNode; titleId?: string; back?: boolean; backId?: string; children: React.ReactNode;
}) {
  const w = useWallet();
  const Tag = as;
  return (
    <Tag id={id} className="screen" hidden={!w.isOpen(id)} aria-labelledby={as === "section" ? titleId : undefined}>
      {title !== undefined && (
        <header className="topbar">
          {back
            ? <button type="button" id={backId} className="icon-btn" data-back aria-label="Back" onClick={() => w.back(id)}><Icon d={ICONS.back} /></button>
            : <span className="icon-btn-space"></span>}
          <h2 id={titleId}>{title}</h2>
          <span className="icon-btn-space"></span>
        </header>
      )}
      {children}
    </Tag>
  );
}

// "Copy" next to a read-only field; says "Copied" for a moment.
export function CopyButton({ id, target, label: text = "Copy", className }: { id?: string; target: string; label?: string; className?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    const input = document.getElementById(target) as HTMLInputElement;
    if (!navigator.clipboard) return input.select();
    navigator.clipboard.writeText(input.value).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }, () => input.select());
  };
  return <button type="button" id={id} className={className} onClick={copy}>{copied ? "Copied" : text}</button>;
}
