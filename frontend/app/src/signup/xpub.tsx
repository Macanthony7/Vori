// Sign-up with pasted xpubs (hardware wallet, Electrum, Sparrow): no phrase anywhere near this page.
import { Fragment, useRef, useState } from "react";
import { AccountResult, CONFLICT, ErrorLine, Shell, createAccount, mount, type Account } from "./shared";

const FIELDS = [
  ["BTC", "Bitcoin (BTC)", "xpub…"],
  ["LTC", "Litecoin (LTC)", "Ltub… or xpub…"],
  ["DOGE", "Dogecoin (DOGE)", "dgub… or xpub…"],
  ["ETH", "Ethereum and BNB Smart Chain (ETH, BSC)", "xpub…"],
  ["TRX", "Tron (TRX)", "xpub…"],
];

function Xpub() {
  const [error, setError] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [account, setAccount] = useState<Account | null>(null);
  const form = useRef<HTMLFormElement>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFieldErrors({});
    setError("");
    const xpubs: Record<string, string> = {};
    new FormData(form.current!).forEach((v, k) => { if (String(v).trim()) xpubs[k] = String(v).trim(); });
    if (!Object.keys(xpubs).length) return setError("Paste at least one xpub.");
    setBusy(true);
    const r = await createAccount(xpubs);
    setBusy(false);
    if ("ok" in r) setAccount(r.ok);
    else if ("conflict" in r) setError(CONFLICT);
    else if ("error" in r) setError(r.error);
    else {
      setFieldErrors(r.errors);
      const first = FIELDS.find(([chain]) => r.errors[chain]);
      if (first) setTimeout(() => document.getElementById("x-" + first[0])?.focus());
    }
  };

  return (
    <Shell>
      <header>
        <a href="/" className="link">&larr; Back</a>
        <h1>Use xpub keys</h1>
        <p className="lede">Paste the account xpub (BIP44, path <code>m/44'/coin'/account'</code>) for each coin you want to accept. Leave the rest empty.</p>
      </header>

      <form id="manual" ref={form} noValidate onSubmit={submit} hidden={!!account}>
        {FIELDS.map(([chain, label, placeholder]) => (
          <Fragment key={chain}>
            <label htmlFor={"x-" + chain}>{label}</label>
            <input id={"x-" + chain} name={chain} placeholder={placeholder} autoComplete="off" spellCheck={false} autoCapitalize="off"
                   aria-invalid={fieldErrors[chain] ? true : undefined} />
            <p className="field-error" id={"e-" + chain} role="alert">{fieldErrors[chain] || ""}</p>
          </Fragment>
        ))}
        <button type="submit" disabled={busy}>Create account</button>
      </form>

      <p className="note">Never paste a private key (<code>xprv</code>) or a recovery phrase here. Don't have xpubs? Hardware wallets, Electrum and Sparrow can export them, or use the <a href="/tools/payment-xpub-tool.html" download>offline xpub tool</a> (<a href="/tools/payment-xpub-tool.html.sha256">SHA-256</a>) on a computer without internet.</p>

      <ErrorLine message={error} />
      {account && <AccountResult account={account} />}
    </Shell>
  );
}

mount(<Xpub />);
