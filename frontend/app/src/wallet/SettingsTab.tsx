// Settings tab: shop name and logo, API key, webhooks, code samples, security. Docs: docs/api.md#webhooks
import { useEffect, useRef, useState } from "react";
import { api, type Delivery } from "./api";
import { useWallet } from "./context";
import { Chevron, CopyButton, Screen } from "./ui";

export function SettingsTab() {
  const w = useWallet();
  const [deliveries, setDeliveries] = useState<Delivery[]>([]);
  const [sampleLoaded, setSampleLoaded] = useState(false);
  const loadDeliveries = async () => {
    try { setDeliveries((await api<{ deliveries: Delivery[] }>("GET", "/accounts/me/webhook/deliveries")).deliveries); } catch { /* keep what's shown */ }
  };
  useEffect(() => { if (w.me) loadDeliveries(); }, [w.me]);

  const row = (id: string, screen: string, name: string, value: React.ReactNode, valueId?: string, also?: () => void) => (
    <li><button type="button" className="row" data-open={screen} id={id} onClick={() => { w.open(screen, "settings-home"); also?.(); }}>
      <span className="row-main">{name}</span><span id={valueId} className="row-value">{value}</span><Chevron />
    </button></li>
  );
  const me = w.me;
  return (
    <>
      <Screen id="settings-home" title="Settings">
        <h3 className="group-title">Shop</h3>
        <ul className="list">
          {row("open-shop", "shop-settings", "Shop name and logo", me ? me.shop_name || "Not set" : "", "shop-summary")}
        </ul>
        <h3 className="group-title">Developer</h3>
        <ul className="list">
          {row("open-key", "api-key-card", "API key", me?.api_key_hint ? me.api_key_hint + "…" : "", "key-summary")}
          {row("open-hooks", "webhooks", "Webhooks", me ? (me.webhook_url ? "On" : "Off") : "", "hook-summary", loadDeliveries)}
          {row("open-integration", "integration", "Connect your shop", "Code samples", undefined, () => setSampleLoaded(true))}
        </ul>
        <h3 className="group-title">Security</h3>
        <ul className="list">
          <li className="row-static"><span>Auto-lock</span><span>After 15 minutes</span></li>
          <li className="row-static"><span>Recovery phrase</span><span>Only on your paper backup</span></li>
          <li><button type="button" className="row" id="lock-settings" onClick={w.lock}><span className="row-main">Lock now</span><Chevron /></button></li>
        </ul>
      </Screen>
      <ShopSettings />
      <Webhooks deliveries={deliveries} reload={loadDeliveries} />
      <ApiKey />
      <Integration active={sampleLoaded} />
    </>
  );
}

// --- Shop name and logo ------------------------------------------------------------------------------
function ShopSettings() {
  const w = useWallet();
  const me = w.me;
  const [name, setName] = useState("");
  const [logo, setLogo] = useState<string | null | undefined>(undefined); // undefined = keep, null = remove, data: URL = new
  const [preview, setPreview] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [result, setResult] = useState("");
  const [busy, setBusy] = useState(false);
  const file = useRef<HTMLInputElement>(null);

  useEffect(() => { // what the server has
    setName(me?.shop_name || "");
    setLogo(undefined);
    setPreview(me?.has_logo ? me.logo || null : null);
  }, [me]);

  const pick = () => {
    const f = file.current!.files?.[0];
    setError("");
    if (!f) return;
    if (!["image/png", "image/jpeg"].includes(f.type) || f.size > 100000) {
      setError("Pick a PNG or JPEG image of at most 100 KB.");
      file.current!.value = "";
      return;
    }
    const reader = new FileReader();
    reader.onload = () => { setLogo(reader.result as string); setPreview(reader.result as string); };
    reader.readAsDataURL(f);
  };
  const remove = () => { setLogo(null); file.current!.value = ""; setPreview(null); };
  const save = async () => {
    const body: Record<string, unknown> = { name: name.trim() };
    if (logo !== undefined) body.logo = logo;
    setError("");
    setResult("");
    setBusy(true);
    try {
      const r = await api<{ shop_name: string | null; has_logo: boolean }>("PUT", "/accounts/me/shop", body);
      setResult("Saved. New checkout pages show " + (r.shop_name ? "“" + r.shop_name + "”" : "no name") + (r.has_logo ? " with your logo." : " without a logo."));
      w.setMe({ ...me, shop_name: r.shop_name, has_logo: r.has_logo, logo: r.has_logo ? preview : null });
    } catch (e) {
      setError((e as Error).message);
    }
    setBusy(false);
  };

  return (
    <Screen id="shop-settings" as="section" title="Shop name and logo" titleId="shop-title" back>
      <div className="sheet-body">
        <p className="note">Payers see these at the top of the checkout page, so they know who they're paying.</p>
        <ul className="list form-list">
          <li><label htmlFor="shop-name">Shop name</label><input id="shop-name" maxLength={60} autoComplete="organization" value={name} onChange={(e) => setName(e.target.value)} /></li>
          <li><label htmlFor="shop-logo">Logo (PNG or JPEG, up to 100 KB)</label><input id="shop-logo" type="file" accept="image/png,image/jpeg" ref={file} onChange={pick} />
            <div id="shop-logo-box" className="logo-preview" hidden={!preview}>
              <img id="shop-logo-preview" alt="Your logo" src={preview || undefined} />
              <button type="button" id="shop-logo-remove" className="link" onClick={remove}>Remove logo</button>
            </div>
          </li>
        </ul>
        <p className="error" id="shop-error" role="alert">{error}</p>
        <button type="button" id="shop-save" className="wide" disabled={busy} onClick={save}>Save</button>
        <p id="shop-result" className="note" role="status">{result}</p>
      </div>
    </Screen>
  );
}

// --- Webhooks ----------------------------------------------------------------------------------------
const DELIVERY: Record<string, string> = { delivered: "Delivered", queued: "Queued", retrying: "Retrying", failed: "Failed" };
const DELIVERY_TONE: Record<string, string> = { delivered: "confirmed", queued: "pending", retrying: "pending", failed: "failed" };

function Webhooks({ deliveries, reload }: { deliveries: Delivery[]; reload: () => void }) {
  const w = useWallet();
  const current = w.me?.webhook_url || null;
  const [url, setUrl] = useState("");
  const [secret, setSecret] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [result, setResult] = useState("");
  const [busy, setBusy] = useState("");
  useEffect(() => { if (current) setUrl(current); }, [current]);

  const act = async (name: string, work: () => Promise<void>) => {
    setError("");
    setResult("");
    setBusy(name);
    try { await work(); } catch (e) { setError((e as Error).message); }
    setBusy("");
  };
  const save = () => act("save", async () => {
    const r = await api<{ url: string; secret: string }>("PUT", "/accounts/me/webhook", { url: url.trim() });
    setSecret(r.secret);
    w.setMe({ ...w.me, webhook_url: r.url });
  });
  const test = () => act("test", async () => {
    setResult("Sending…");
    const r = await api<{ delivered: boolean; error?: string }>("POST", "/accounts/me/webhook/test");
    setResult(r.delivered ? "Your shop answered OK: test event delivered."
      : `Not delivered: ${r.error}. Check the URL, and that your server answers 2xx quickly.`);
    reload();
  });
  const remove = () => act("remove", async () => {
    await api("DELETE", "/accounts/me/webhook");
    setUrl("");
    setSecret(null);
    w.setMe({ ...w.me, webhook_url: null });
  });

  return (
    <Screen id="webhooks" as="section" title="Webhooks" titleId="hooks-title" back>
      <div className="sheet-body">
        <p className="note">When a payment changes status, we send a signed message to your shop's server, so it can mark the order paid.</p>
        <p id="hook-current">{w.me ? (current ? "Sending events to " + current : "No webhook set: your shop only learns about payments by asking the API.") : ""}</p>
        <ul className="list form-list">
          <li><label htmlFor="hook-url">Webhook URL</label>
            <input id="hook-url" type="url" inputMode="url" placeholder="https://yourshop.com/hooks/crypto" autoComplete="off" spellCheck={false} autoCapitalize="off"
                   value={url} onChange={(e) => setUrl(e.target.value)} /></li>
        </ul>
        <p className="error" id="hook-error" role="alert">{error}</p>
        <div className="actions">
          <button type="button" id="hook-save" disabled={busy === "save"} onClick={save}>Save</button>
          <button type="button" id="hook-test" className="secondary" hidden={!current} disabled={busy === "test"} onClick={test}>Send test event</button>
          <button type="button" id="hook-remove" className="secondary" hidden={!current} disabled={busy === "remove"} onClick={remove}>Remove</button>
        </div>
        <div id="hook-secret-box" hidden={!secret}>
          <label htmlFor="hook-secret">Signing secret</label>
          <div className="copy-row">
            <input id="hook-secret" readOnly value={secret || ""} />
            <CopyButton id="hook-secret-copy" target="hook-secret" />
          </div>
          <p className="warning">Put it in your shop's server settings now. It's shown only once; saving the URL again makes a new one.</p>
        </div>
        <p id="hook-result" className="note" role="status">{result}</p>
        <h3 className="group-title">Recent deliveries</h3>
        <p id="hook-empty" className="note" hidden={!!deliveries.length}>None yet. Events appear here as payments change status.</p>
        <ul id="hook-deliveries" className="list" hidden={!deliveries.length}>
          {deliveries.map((d, i) => {
            let state = DELIVERY[d.status] || d.status;
            if (d.last_error && d.status !== "delivered") state += ` (${d.last_error})`;
            return (
              <li key={i} className="row-static delivery">
                <span className="row-main"><span className="row-title">{d.event}</span><span className="row-sub mono">{d.payment_id}</span></span>
                <span className="mark" data-state={DELIVERY_TONE[d.status] || ""}>{state}</span>
              </li>
            );
          })}
        </ul>
      </div>
    </Screen>
  );
}

// --- API key -------------------------------------------------------------------------------------------
function ApiKey() {
  const w = useWallet();
  const [armed, setArmed] = useState(false);
  const [fresh, setFresh] = useState<string | null>(null);
  const me = w.me;
  const make = async () => {
    // Two clicks: the old key stops working, which breaks the shop until it's updated.
    if (!armed) return setArmed(true);
    setArmed(false);
    try {
      const r = await api<{ api_key: string }>("POST", "/accounts/me/api-key");
      setFresh(r.api_key);
      w.setMe({ ...me, api_key_hint: r.api_key.slice(0, 7), api_key_used_at: undefined });
    } catch (e) {
      w.showError(`Couldn't make a new API key (${(e as Error).message}).`);
    }
  };
  const hint = me?.api_key_hint;
  return (
    <Screen id="api-key-card" as="section" title="API key" titleId="key-title" back>
      <div className="sheet-body">
        <p>Your shop uses an API key to create payments. Lost it? Make a new one: the old key stops working immediately.</p>
        <p id="key-info" className="note">{me ? (hint ? `Your current key starts with ${hint}…` : "Your key was made before key hints existed.") +
          " Last used: " + (me.api_key_used_at ? new Date(me.api_key_used_at).toLocaleString() : "not yet") + "." : ""}</p>
        <p className="note">Keep the key on your shop's server only, never in web pages or apps. A leaked key can create and read payments, but can't move funds or change these settings: those need this wallet.</p>
        <button type="button" id="new-key" className="secondary" onClick={make}>{armed ? "Click again: replace my API key" : "Make a new API key"}</button>
        <div id="new-key-box" hidden={!fresh}>
          <div className="copy-row">
            <input id="new-key-value" readOnly aria-label="New API key" value={fresh || ""} />
            <CopyButton id="new-key-copy" target="new-key-value" />
          </div>
          <p className="warning">Save it now and update your shop. It's shown only once.</p>
        </div>
      </div>
    </Screen>
  );
}

// --- Connect your shop: tested code samples ---------------------------------------------------------------
const SAMPLES = [["python.py", "Python"], ["node.js", "Node.js"], ["php.php", "PHP"], ["curl.sh", "curl"]];

function Integration({ active }: { active: boolean }) {
  const [file, setFile] = useState("python.py");
  const [text, setText] = useState("");
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!active) return;
    let gone = false;
    fetch("/samples/" + file, { cache: "no-cache" })
      .then((res) => { if (!res.ok) throw new Error("HTTP " + res.status); return res.text(); })
      .then((t) => t.split("https://pay.example.com").join(location.origin), (e) => `Couldn't load the sample (${e.message}).`)
      .then((t) => { if (!gone) setText(t); });
    return () => { gone = true; };
  }, [active, file]);
  const copy = () => {
    if (!navigator.clipboard) return;
    navigator.clipboard.writeText(text).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); });
  };
  return (
    <Screen id="integration" as="section" title="Connect your shop" titleId="integration-title" back>
      <div className="sheet-body">
        <ol className="steps">
          <li>Put your API key in your shop's <strong>server</strong> settings (an environment variable such as <code>GATEWAY_API_KEY</code>). Never in web pages or apps.</li>
          <li>When a customer checks out, your server creates a payment and sends the customer to its <code>checkout_url</code>.</li>
          <li>Set a webhook in Settings. When it says <code>payment.confirmed</code>, check the signature and mark the order paid.</li>
        </ol>
        <p>API address: <code id="api-base">{active ? location.origin : ""}</code></p>
        <div className="segmented" role="group" aria-label="Code sample language">
          {SAMPLES.map(([f, name]) => <button key={f} type="button" data-sample={f} aria-pressed={f === file} onClick={() => setFile(f)}>{name}</button>)}
        </div>
        <pre id="sample" className="sample" tabIndex={0} aria-label="Code sample"><code>{text}</code></pre>
        <button type="button" id="sample-copy" className="secondary" onClick={copy}>{copied ? "Copied" : "Copy code"}</button>
        <p className="note">Every sample is tested against the gateway. Full reference: the API docs in the gateway's <code>docs/api.md</code>.</p>
      </div>
    </Screen>
  );
}
