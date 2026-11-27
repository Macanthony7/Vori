// Shared by the sign-up pages (index.html, xpub.html). This code can talk to our server, so it must
// never see a recovery phrase: phrases live only in the sandboxed derive.html frame.
import { Fragment, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "../../../public/styles.css";

export type Account = { account_id: string; api_key: string; chains: Record<string, { first_address: string }> };
export type Created = { ok: Account } | { errors: Record<string, string> } | { conflict: true } | { error: string };

export const CONFLICT = "These keys are already registered to an account. Use that account's API key, or use a different account number.";
const CHAIN_NAMES: Record<string, string> = { BTC: "Bitcoin", LTC: "Litecoin", DOGE: "Dogecoin", ETH: "Ethereum + BSC", TRX: "Tron" };

export function mount(page: React.ReactNode) {
  createRoot(document.getElementById("root")!).render(page);
}

// POST /accounts
export async function createAccount(xpubs: Record<string, string>): Promise<Created> {
  let res: Response;
  try {
    res = await fetch("/accounts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ xpubs }),
    });
  } catch {
    return { error: "Couldn't reach the server. Check your connection and try again." };
  }
  const data = await res.json().catch(() => ({}));
  const detail = data.detail || {};
  if (res.status === 201) return { ok: data };
  if (detail.errors) return { errors: detail.errors };
  if (res.status === 409) return { conflict: true };
  return { error: detail.error || `Something went wrong (HTTP ${res.status}). Please try again.` };
}

export function Shell({ children }: { children: React.ReactNode }) {
  return (
    <>
      <header className="shell">
        <div className="shell-inner">
          <p className="brand"><span className="brand-mark" aria-hidden="true"></span>Self-custody payments</p>
        </div>
      </header>
      <main>{children}</main>
    </>
  );
}

export function ErrorLine({ message }: { message: string }) {
  return <p className="error" id="error" role="alert" hidden={!message}>{message}</p>;
}

// The new account: its API key (shown once) and the first address per coin to check against the wallet.
export function AccountResult({ account, children }: { account: Account; children?: React.ReactNode }) {
  const box = useRef<HTMLElement>(null);
  const [copied, setCopied] = useState(false);
  useEffect(() => { box.current!.scrollIntoView(); }, []);
  const copy = () => {
    const select = () => (document.getElementById("api-key") as HTMLInputElement).select();
    if (!navigator.clipboard) return select();
    navigator.clipboard.writeText(account.api_key).then(() => setCopied(true), select);
  };
  return (
    <section id="result" ref={box}>
      <h2>Your account is ready</h2>
      {children}
      <label htmlFor="api-key">API key</label>
      <div className="copy-row">
        <input id="api-key" readOnly value={account.api_key} />
        <button type="button" id="copy-key" onClick={copy}>{copied ? "Copied" : "Copy"}</button>
      </div>
      <p className="warning">Save this key now. It's shown only once and it's your only login: we have no email to reset it.</p>
      <h3>Check your addresses</h3>
      <p>The first receive address for each coin should match the one your wallet shows for the same account.</p>
      <dl id="addresses" className="addresses">
        {Object.entries(account.chains).map(([chain, c]) => (
          <Fragment key={chain}>
            <dt>{CHAIN_NAMES[chain] || chain}</dt>
            <dd>{c.first_address}</dd>
          </Fragment>
        ))}
      </dl>
    </section>
  );
}
