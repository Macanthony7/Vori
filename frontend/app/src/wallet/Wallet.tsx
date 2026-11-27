// Wallet page. Holds only the password-encrypted vault (saved by the sign-up page) and hands it to
// the sandboxed, offline frame, which asks for the password and keeps the unlocked keys.
// After unlock the frame signs a login challenge, and the dashboard uses the resulting session.
// This page can reach our server, so it must never see the password or the phrase.
// Layout: three tabs (Wallet, Payments, Settings), each a stack of screens. Docs: DESIGN.md#wallet,
// docs/security.md#wallet-vault, docs/api.md#wallet
import { useCallback, useEffect, useRef, useState } from "react";
import { api, setSession, hasSession, stream, type Balance, type MarketEntry, type Me, type Payment } from "./api";
import { Ctx, type Tab, type WalletCtx } from "./context";
import { CURRENCY, LockIcon, TRON } from "./ui";
import { WalletTab } from "./WalletTab";
import { PaymentsTab } from "./PaymentsTab";
import { SettingsTab } from "./SettingsTab";

const VAULT_KEY = "wallet.vault.v1"; // written by the sign-up page (frontend/app/src/signup)
const FRAME_TIMEOUT_MS = 12000;      // same as sign-up: the inlined engine is ~3.6 MB
const TOPUP_WAIT_MS = 180000;
const EXPLORER: Record<string, string> = {
  BTC: "https://blockchair.com/bitcoin/transaction/",
  LTC: "https://blockchair.com/litecoin/transaction/",
  DOGE: "https://blockchair.com/dogecoin/transaction/",
  ETH: "https://etherscan.io/tx/",
  BSC: "https://bscscan.com/tx/",
  SEPOLIA: "https://sepolia.etherscan.io/tx/",
  TRX: "https://tronscan.org/#/transaction/",
  NILE: "https://nile.tronscan.org/#/transaction/",
};
const TABS: Tab[] = ["wallet", "payments", "settings"];
const FIRST: Record<Tab, string> = { wallet: "home", payments: "payments-home", settings: "settings-home" };
const PANEL_OF: Record<string, Tab> = {
  home: "wallet", picker: "wallet", coin: "wallet", receive: "wallet", send: "wallet", charge: "wallet",
  "payments-home": "payments", "payment-detail": "payments",
  "settings-home": "settings", "shop-settings": "settings", webhooks: "settings", "api-key-card": "settings", integration: "settings",
};

type Saved = { vault: unknown; account_id?: string | null; saved_at?: string };
type Phase = "locked" | "signing" | "open";

function loadSaved(): Saved | null {
  try { return JSON.parse(localStorage.getItem(VAULT_KEY) || "null"); } catch { return null; }
}

// Explorer links for broadcast transactions.
function txLinks(chain: string, hashes: string[], name: string) {
  return hashes.map((hash, i) => (
    <span key={hash}>
      <a href={EXPLORER[chain] + encodeURIComponent(hash.replace(/^0x/, chain in TRON ? "" : "0x"))} rel="noreferrer" target="_blank">
        {name + (hashes.length > 1 ? " " + (i + 1) : "")}
      </a>{" "}
    </span>
  ));
}

export function Wallet() {
  const [saved, setSaved] = useState<Saved | null>(loadSaved);
  const [phase, setPhase] = useState<Phase>("locked");
  const [error, setError] = useState("");
  // The frame: always mounted while there's a vault; hidden while the dashboard shows.
  const frame = useRef<HTMLIFrameElement>(null);
  const [frameSrc, setFrameSrc] = useState("about:blank");
  const [frameShown, setFrameShown] = useState(true);
  const [frameWaiting, setFrameWaiting] = useState(true);
  const [frameHeight, setFrameHeight] = useState<number | null>(null);
  const [vaultShown, setVaultShown] = useState(true);
  const [forgetShown, setForgetShown] = useState(true);
  const [signingNote, setSigningNote] = useState(true);
  const timer = useRef(0);
  const loads = useRef(0);
  // Navigation
  const [tab, setTab] = useState<Tab>("wallet");
  const [screens, setScreens] = useState<Record<Tab, string>>(FIRST);
  const before = useRef<Record<string, string>>({}); // screen -> the screen that opened it (for Back)
  const tabButtons = useRef<Record<string, HTMLButtonElement | null>>({});
  // Data
  const [balances, setBalances] = useState<Balance[]>([]);
  const [balancesLoaded, setBalancesLoaded] = useState(false);
  const [market, setMarket] = useState<Record<string, MarketEntry>>({});
  const [payments, setPayments] = useState<Payment[]>([]);
  const [struck, setStruck] = useState<Set<string>>(new Set());
  const lastStatus = useRef<Record<string, string>>({});
  const [me, setMe] = useState<Me | null>(null);
  // Screens opened with something to show
  const [coin, setCoin] = useState<Balance | null>(null);
  const [receiving, setReceiving] = useState<Balance | null>(null);
  const [sending, setSending] = useState<{ b: Balance; n: number } | null>(null);
  const [charge, setCharge] = useState<{ b: Balance | null; n: number }>({ b: null, n: 0 });
  const [payment, setPayment] = useState<Payment | null>(null);
  const [sendError, setSendError] = useState("");
  const [reviewing, setReviewing] = useState(false);
  const [sendResult, setSendResult] = useState<React.ReactNode>(null);

  const showError = setError;
  const post = (message: unknown, transfer: Transferable[] = []) =>
    // The frame's origin is opaque ("null"), so "*" is the only target; it's our own frame (src below).
    frame.current?.contentWindow?.postMessage(message, "*", transfer);

  // --- Navigation ---------------------------------------------------------------------------------
  const open = useCallback((id: string, from?: string) => {
    const panel = PANEL_OF[id];
    setScreens((s) => ({ ...s, [panel]: id }));
    if (from) before.current[id] = from;
    window.scrollTo(0, 0);
  }, []);
  const back = (id: string) => {
    const panel = PANEL_OF[id], prev = before.current[id];
    open(prev && PANEL_OF[prev] === panel ? prev : FIRST[panel]);
  };
  const showTab = (name: Tab, focus = false) => {
    if (name === tab && !focus) open(FIRST[name]); // tap the open tab again: back to its start
    setTab(name);
    if (focus) tabButtons.current[name]?.focus();
    window.scrollTo(0, 0);
  };

  // --- Data -----------------------------------------------------------------------------------------
  const loadMarket = async () => {
    try {
      const data = await api<{ coins: Record<string, { price: string | null; change_24h: string | null }> }>("GET", "/market?currency=" + CURRENCY);
      const next: Record<string, MarketEntry> = {};
      for (const [c, m] of Object.entries(data.coins)) {
        next[c] = { price: m.price == null ? null : Number(m.price), change: m.change_24h == null ? null : Number(m.change_24h) };
      }
      setMarket(next);
    } catch { /* prices are a nicety: balances still show without them */ }
  };
  const loadBalances = async () => {
    try {
      const data = await api<{ balances: Balance[] }>("GET", "/wallet/balances");
      setBalances(data.balances);
      setBalancesLoaded(true);
    } catch (e) {
      showError(`Couldn't load balances (${(e as Error).message}).`);
    }
  };
  const loadPayments = async () => {
    try {
      const data = await api<{ payments: Payment[] }>("GET", "/payments?limit=100");
      const changed = new Set(data.payments.filter((p) => lastStatus.current[p.id] && lastStatus.current[p.id] !== p.status).map((p) => p.id));
      data.payments.forEach((p) => { lastStatus.current[p.id] = p.status; });
      setStruck(changed);
      setPayments(data.payments);
    } catch (e) {
      showError(`Couldn't load payments (${(e as Error).message}).`);
    }
  };
  const loadAccount = async () => {
    try { setMe(await api<Me>("GET", "/accounts/me")); } catch { setMe(null); }
  };
  const loadDashboard = () => { loadBalances(); loadPayments(); loadAccount(); loadMarket(); };

  // --- Lock / unlock --------------------------------------------------------------------------------
  // Locking = reloading the frame (everything it had in memory is gone) and ending the session.
  const startFrame = () => {
    if (hasSession()) api("DELETE", "/auth/session").catch(() => {});
    setSession(null);
    setPhase("locked");
    setVaultShown(true);
    setForgetShown(true);
    setFrameWaiting(true);
    setFrameShown(true);
    setError("");
    clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      setFrameWaiting(false);
      setFrameShown(false);
      setFrameSrc("about:blank");
      setError("Your browser blocked the secure wallet window, so it can't run here. Open this page in Chrome, Edge, Firefox or Safari.");
    }, FRAME_TIMEOUT_MS);
    // A new URL every time: re-setting the same "derive.html#unlock" is only a fragment change, which
    // does NOT reload the frame, so the unlocked keys would stay in its memory.
    setFrameSrc(`/derive.html?load=${++loads.current}#unlock`);
  };

  const signIn = async () => {
    setVaultShown(false); // the frame stays loaded, just out of sight
    setSigningNote(true);
    setPhase("signing");
    try {
      const c = await api<{ challenge: string }>("POST", "/auth/challenge");
      post({ type: "sign-login", challenge: c.challenge });
    } catch (e) {
      showError(`Couldn't reach the server (${(e as Error).message}). Reload and try again.`);
    }
  };

  const finishSignIn = async (msg: { xpub: string; challenge: string; signature: string }) => {
    try {
      const r = await api<{ token: string }>("POST", "/auth/login", { xpub: msg.xpub, challenge: msg.challenge, signature: msg.signature });
      setSession(r.token);
    } catch (e) {
      setSigningNote(false);
      const m = (e as Error).message;
      showError(m === "no account for this wallet"
        ? "This wallet has no account with us yet. Set it up again from the start page with the same recovery phrase."
        : `Sign-in failed (${m}). Reload and try again.`);
      return;
    }
    setPhase("open");
    setScreens(FIRST);
    setTab("wallet");
    window.scrollTo(0, 0);
    loadDashboard();
  };

  const showSigner = () => {
    setForgetShown(false);
    setVaultShown(true);
    setTimeout(() => document.getElementById("vault")?.scrollIntoView({ block: "start" }));
  };
  const hideSigner = () => {
    setVaultShown(false);
    setForgetShown(true);
  };

  // --- Send: the frame signed; relay it ---------------------------------------------------------------
  // The frame asked for the signing engine. It checks every file's SHA-256 itself before running it.
  const sendEngine = async (names: string[]) => {
    const files: Record<string, ArrayBuffer> = {}, transfer: ArrayBuffer[] = [];
    try {
      for (const name of names) {
        if (!/^[a-z_.-]+$/.test(name)) continue;
        const res = await fetch("/vendor/wallet-core/" + name);
        if (!res.ok) throw new Error("HTTP " + res.status);
        files[name] = await res.arrayBuffer();
        transfer.push(files[name]);
      }
    } catch (err) {
      showError(`Couldn't download the signing engine (${(err as Error).message}). Check your connection and try again.`);
    }
    post({ type: "engine-files", files }, transfer);
  };

  // Gas top-ups must land before the token transfers that spend them are relayed.
  const waitFor = async (chain: string, hashes: string[]) => {
    const until = Date.now() + TOPUP_WAIT_MS;
    while (Date.now() < until) {
      await new Promise((r) => setTimeout(r, 3000));
      const states = await Promise.all(hashes.map((h) =>
        api<{ done: boolean; ok?: boolean }>("GET", `/wallet/tx/${chain}/${encodeURIComponent(h)}`).catch(() => ({ done: false, ok: undefined }))));
      if (states.some((st) => st.done && !st.ok)) return "a gas top-up failed on the network";
      if (states.every((st) => st.done)) return null;
    }
    return "the gas top-ups haven't confirmed yet";
  };

  const sentHome = () => { setTab("wallet"); open("home"); }; // the result shows on the wallet's first screen

  type Signed = { chain: string; txs: string[]; topups?: string[]; symbol: string; amount: string; to: string; fee: string };
  const broadcastSigned = async (msg: Signed) => {
    hideSigner();
    setReviewing(false);
    const topups = msg.topups || [];
    if (topups.length) {
      let t: { tx_hashes: string[]; error?: string };
      try {
        t = await api("POST", "/wallet/send/" + msg.chain, { txs: topups });
      } catch (err) {
        setSendError("Not sent: " + (err as Error).message);
        return;
      }
      sentHome();
      setSendResult(<>{`Sending gas from your main address to ${topups.length} payment address${topups.length > 1 ? "es" : ""}, then the transfer. Keep this page open… `}{txLinks(msg.chain, t.tx_hashes, "Gas top-up")}</>);
      const problem = t.error || await waitFor(msg.chain, t.tx_hashes);
      if (problem) {
        setSendResult(<>{`Not sent: ${problem}. No ${msg.symbol} moved. Any gas that arrived stays on those addresses; try Send again in a minute. `}{txLinks(msg.chain, t.tx_hashes, "Gas top-up")}</>);
        loadBalances();
        return;
      }
    }
    let r: { tx_hashes: string[]; error?: string };
    try {
      r = await api("POST", "/wallet/send/" + msg.chain, { txs: msg.txs });
    } catch (err) {
      if (topups.length) setSendResult(`Gas was sent, but the transfer wasn't: ${(err as Error).message}. Try Send again.`);
      else setSendError("Not sent: " + (err as Error).message);
      return;
    }
    sentHome();
    setSendResult(<>
      {r.error ? `Partly sent: ${r.tx_hashes.length} of ${msg.txs.length} transfers went through. ${r.error} `
               : `Sent ${msg.amount} ${msg.symbol} to ${msg.to} (network fee ${msg.fee}). `}
      {txLinks(msg.chain, r.tx_hashes, r.tx_hashes.length > 1 ? "Transfer" : "View transaction")}
    </>);
    loadBalances();
  };

  // --- Messages from the frame ----------------------------------------------------------------------
  const onMessage = useRef<(e: MessageEvent) => void>(() => {});
  onMessage.current = (e: MessageEvent) => {
    if (!frame.current || e.source !== frame.current.contentWindow || !e.data) return;
    clearTimeout(timer.current);
    setFrameWaiting(false);
    const d = e.data;
    if (d.type === "ready") post({ type: "vault", vault: saved!.vault });
    else if (d.type === "height") setFrameHeight(Math.min(Number(d.height) || 0, 2000) + 24);
    else if (d.type === "unlocked") signIn();
    else if (d.type === "vault-upgraded" && d.vault) {
      const next = { ...saved!, vault: d.vault }; // still only ciphertext
      setSaved(next);
      try { localStorage.setItem(VAULT_KEY, JSON.stringify(next)); } catch { /* keep using v1 */ }
    } else if (d.type === "login-signature") finishSignIn(d);
    else if (d.type === "engine-request") sendEngine(d.files);
    else if (d.type === "send-signed") broadcastSigned(d);
    else if (d.type === "send-cancelled") { hideSigner(); setReviewing(false); }
    else if (d.type === "locked") startFrame(); // auto-lock after 15 minutes
    else if (d.type === "frame-error") showError(`The secure wallet window failed to load (${String(d.message).slice(0, 200)}). Please reload.`);
  };

  useEffect(() => {
    const listen = (e: MessageEvent) => onMessage.current(e);
    window.addEventListener("message", listen);
    if (saved && saved.vault) startFrame();
    return () => window.removeEventListener("message", listen);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { document.body.classList.toggle("unlocked", phase === "open"); }, [phase]);

  // Live prices while unlocked (GET /market/stream, display only). On a drop, reconnect after a few
  // seconds; meanwhile the prices from GET /market stay.
  useEffect(() => {
    if (phase !== "open") return;
    const stop = new AbortController();
    (async () => {
      while (!stop.signal.aborted) {
        try {
          await stream("/market/stream", (data: { coins: Record<string, { price: string | null; change_24h: string | null }> }) => {
            setMarket((m) => {
              const next = { ...m };
              for (const [c, v] of Object.entries(data.coins)) {
                next[c] = { price: v.price == null ? null : Number(v.price), change: v.change_24h == null ? m[c]?.change ?? null : Number(v.change_24h) };
              }
              return next;
            });
          }, stop.signal);
        } catch { /* dropped or refused: try again below */ }
        await new Promise((r) => setTimeout(r, 3000));
      }
    })();
    return () => stop.abort();
  }, [phase]);
  // Height through the CSSOM: the CSP allows no inline style attributes.
  useEffect(() => { if (frame.current) frame.current.style.height = frameHeight == null ? "" : frameHeight + "px"; }, [frameHeight]);

  const forget = () => {
    try { localStorage.removeItem(VAULT_KEY); } catch { /* nothing saved, nothing to remove */ }
    setFrameSrc("about:blank");
    clearTimeout(timer.current);
    setSaved(null);
  };

  const ctx: WalletCtx = {
    isOpen: (id) => screens[PANEL_OF[id]] === id,
    open, back, showTab: (t) => showTab(t), showError, lock: startFrame,
    balances, balancesLoaded, market, payments, struck, me, setMe, loadDashboard, loadBalances, loadPayments,
    coin: coin && (balances.find((b) => b.chain === coin.chain && b.asset === coin.asset) || coin),
    showCoin: (b) => { setCoin(b); open("coin", "home"); },
    showReceive: (b, from) => { setReceiving(b); open("receive", from); },
    receiving,
    showSend: (b, from) => {
      setSending((s) => ({ b, n: (s?.n || 0) + 1 }));
      setSendError("");
      setReviewing(false);
      open("send", from);
      setTimeout(() => document.getElementById("send-amount")?.focus());
    },
    sending,
    showCharge: (b, from) => {
      setCharge((c) => ({ b, n: c.n + 1 }));
      open("charge", from);
      setTimeout(() => document.getElementById("charge-amount")?.focus());
    },
    charge,
    showPayment: (p, from) => {
      setPayment(p);
      if (from === "coin") setTab("payments");
      open("payment-detail", "payments-home");
    },
    payment,
    post, sendError, setSendError, reviewing, setReviewing, showSigner, sendResult,
  };

  const hasVault = !!(saved && saved.vault);
  const unlocked = phase === "open";
  const onTabKey = (e: React.KeyboardEvent, i: number) => { // tablist pattern: arrows move between sections
    let step = ({ ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 } as Record<string, number>)[e.key];
    if (e.key === "Home") step = -i;
    if (e.key === "End") step = TABS.length - 1 - i;
    if (step === undefined) return;
    e.preventDefault();
    showTab(TABS[(i + step + TABS.length) % TABS.length], true);
  };
  const TAB_ICONS: Record<Tab, React.ReactNode> = {
    wallet: <svg className="i" viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="6" width="18" height="13" rx="3" /><path d="M3 10h18M16 14.5h2" /></svg>,
    payments: <svg className="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12h4l3 7 4-14 3 7h4" /></svg>,
    settings: <svg className="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h9M17 7h3M4 17h3M11 17h9" /><circle cx="15" cy="7" r="2" /><circle cx="9" cy="17" r="2" /></svg>,
  };

  return (
    <Ctx.Provider value={ctx}>
      <div className="layout">
        <nav id="tabs" className="nav" aria-label="Wallet" hidden={!unlocked}>
          <p className="nav-brand"><span className="brand-mark" aria-hidden="true"></span>Your wallet</p>
          <div className="nav-tabs" role="tablist" aria-label="Wallet sections">
            {TABS.map((t, i) => (
              <button key={t} type="button" role="tab" id={"tab-" + t} aria-controls={"panel-" + t} aria-selected={tab === t}
                      tabIndex={tab === t ? 0 : -1} ref={(el) => { tabButtons.current[t] = el; }}
                      onClick={() => showTab(t)} onKeyDown={(e) => onTabKey(e, i)}>
                {TAB_ICONS[t]}<span>{t[0].toUpperCase() + t.slice(1)}</span>
              </button>
            ))}
          </div>
          <button type="button" id="lock" className="nav-lock" onClick={startFrame}><LockIcon /><span>Lock</span></button>
        </nav>

        <main className="app-main">
          <section id="none" className="narrow" hidden={hasVault}>
            <h2>No wallet on this device</h2>
            <p>This browser has no saved wallet. Your funds are on the blockchain, not here: set the wallet up again with its recovery phrase.</p>
            <p><a href="/">Create a wallet, or restore one with your recovery phrase</a></p>
          </section>

          <section id="vault" className="narrow" hidden={!hasVault || !vaultShown}>
            <p id="frame-status" className="note" role="status" hidden={!frameWaiting}>Loading the secure wallet window…</p>
            {/* Same offline, sandboxed frame as sign-up. The password and the unlocked keys stay inside it.
                It stays loaded (hidden) while the dashboard is open: it holds the keys for signing. */}
            <iframe id="frame" ref={frame} sandbox="allow-scripts" title="Wallet unlock, isolated and offline" src={frameSrc} hidden={!frameShown}></iframe>
            <details id="forget-box" hidden={!forgetShown}>
              <summary>Remove this wallet from this device</summary>
              <p className="warning">Your funds stay safe on the blockchain. To use the wallet here again, you'll need your recovery phrase.</p>
              <button type="button" id="forget" className="secondary" onClick={forget}>Remove from this device</button>
            </details>
          </section>

          <section id="dashboard" hidden={!hasVault || phase === "locked"}>
            <p id="signing-in" className="note loading" role="status" hidden={unlocked || !signingNote}>Signing in…</p>
            <div id="content" hidden={!unlocked}>
              <section id="panel-wallet" className="panel-tab" role="tabpanel" aria-labelledby="tab-wallet" hidden={tab !== "wallet"}><WalletTab /></section>
              <section id="panel-payments" className="panel-tab" role="tabpanel" aria-labelledby="tab-payments" hidden={tab !== "payments"}><PaymentsTab /></section>
              <section id="panel-settings" className="panel-tab" role="tabpanel" aria-labelledby="tab-settings" hidden={tab !== "settings"}><SettingsTab /></section>
            </div>
          </section>

          <p className="error" id="error" role="alert" hidden={!error}>{error}</p>
        </main>
      </div>
    </Ctx.Provider>
  );
}
