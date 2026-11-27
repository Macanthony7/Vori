// Sign-up: create or import a wallet. The phrase is typed and kept only in the sandboxed, offline
// derive.html frame; this page gets public keys (xpubs) and the password-encrypted vault from it.
import { useEffect, useRef, useState } from "react";
import { AccountResult, CONFLICT, ErrorLine, Shell, createAccount, mount, type Account } from "./shared";

const VAULT_KEY = "wallet.vault.v1"; // also read by wallet.js. Holds only the password-encrypted vault.
// The frame says something (its height) as soon as its script runs. If it stays silent, the browser
// blocked sandboxed scripts: say so instead of showing an empty box.
const FRAME_TIMEOUT_MS = 12000; // the inlined engine is ~3.6 MB; slow phones need a few seconds
const BLOCKED = "Your browser blocked the secure phrase window, so it can't run here. Open this page in Chrome, Edge, Firefox or Safari, or go back and choose \"Use xpub keys\".";

function saveVault(vault: unknown, accountId: string | null): boolean {
  try {
    localStorage.setItem(VAULT_KEY, JSON.stringify({ vault, account_id: accountId, saved_at: new Date().toISOString() }));
    return true;
  } catch {
    return false; // private mode or storage blocked
  }
}

function hasVault(): boolean {
  try { return !!localStorage.getItem(VAULT_KEY); } catch { return false; }
}

type Stage = "choose" | "wallet" | "restored" | "result" | "stopped"; // stopped: only the error is shown

function Signup() {
  const [stage, setStage] = useState<Stage>("choose");
  const [error, setError] = useState("");
  const [src, setSrc] = useState("about:blank");
  const [frameShown, setFrameShown] = useState(true);
  const [waiting, setWaiting] = useState(false); // "Loading the secure phrase window…"
  const [height, setHeight] = useState<number | null>(null);
  const [account, setAccount] = useState<Account | null>(null);
  const [saved, setSaved] = useState<boolean | null>(null);
  const [existing] = useState(hasVault);
  const frame = useRef<HTMLIFrameElement>(null);
  const timer = useRef(0);

  const start = (mode: "create" | "import") => {
    setError("");
    setWaiting(true);
    setFrameShown(true);
    clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      setWaiting(false);
      setFrameShown(false); // don't leave an empty box above the message
      setSrc("about:blank");
      setError(BLOCKED);
    }, FRAME_TIMEOUT_MS);
    setSrc("/derive.html#" + mode);
    setStage("wallet");
  };

  const back = () => {
    clearTimeout(timer.current);
    setSrc("about:blank"); // drops the frame and anything typed in it
    setStage("choose");
    setError("");
  };

  useEffect(() => {
    const onMessage = async (e: MessageEvent) => {
      if (!frame.current || e.source !== frame.current.contentWindow || !e.data) return;
      clearTimeout(timer.current);
      setWaiting(false);
      if (e.data.type === "frame-error") {
        setError(`The secure phrase frame failed to load (${String(e.data.message).slice(0, 200)}). Please reload, or use xpub keys instead.`);
      } else if (e.data.type === "height") {
        setHeight(Math.min(Number(e.data.height) || 0, 2000) + 24);
      } else if (e.data.type === "xpubs") {
        const xpubs = e.data.xpubs || {};
        if (!Object.values(xpubs).every((v) => typeof v === "string")) return;
        const vault = e.data.vault;
        setError("");
        const r = await createAccount(xpubs);
        if ("error" in r) return setError(r.error);
        if ("errors" in r) return setError("The derived keys were rejected. Please reload and try again.");
        // Created, or already registered (the same wallet on a new device): either way the wallet is
        // the merchant's, so keep its encrypted vault on this device.
        setSrc("about:blank");
        const ok = !!vault && saveVault(vault, "ok" in r ? r.ok.account_id : null);
        if ("conflict" in r) {
          if (ok) setStage("restored");
          else { setStage("stopped"); setError(CONFLICT); }
          return;
        }
        setAccount(r.ok);
        setSaved(ok);
        setStage("result");
      }
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  // Keep the frame's height in step with its content (set through the CSSOM: the CSP has no inline styles).
  useEffect(() => {
    if (frame.current) frame.current.style.height = height == null ? "" : height + "px";
  }, [height]);

  return (
    <Shell>
      <section id="choose" className="welcome" hidden={stage !== "choose"}>
        <span className="brand-mark welcome-mark" aria-hidden="true"></span>
        <h1>Accept crypto payments</h1>
        <p className="lede">A wallet that's yours alone. No email, no personal details, no custody: payments go straight to your own addresses.</p>
        <p id="existing" className="warning" hidden={!existing}>A wallet is already saved on this device. <a href="/wallet.html">Open your wallet</a>. Creating or importing another one here replaces it on this device, so make sure you have its recovery phrase.</p>
        <div className="options">
          <button type="button" className="option option-main" data-mode="create" onClick={() => start("create")}>Create a new wallet</button>
          <button type="button" className="option" data-mode="import" onClick={() => start("import")}>Import an existing wallet</button>
          <a className="option option-quiet" href="/xpub.html">Use xpub keys instead (hardware wallet, Electrum, Sparrow)</a>
        </div>
        <p className="note">Your recovery phrase is handled in an isolated window that cannot connect to the internet. Only public keys, which can receive but never spend, reach our server.</p>
      </section>

      <section id="wallet" hidden={stage !== "wallet"}>
        <button type="button" id="back" className="link" onClick={back}>&larr; Back</button>
        <p id="frame-status" className="note" role="status" hidden={!waiting}>Loading the secure phrase window…</p>
        <iframe id="frame" ref={frame} sandbox="allow-scripts" title="Recovery phrase, isolated and offline" src={src} hidden={!frameShown}></iframe>
      </section>

      <ErrorLine message={error} />

      <section id="restored" hidden={stage !== "restored"}>
        <h2>Wallet restored on this device</h2>
        <p>This wallet already has an account with us. Its encrypted copy is now saved on this device too.</p>
        <p><a href="/wallet.html">Open your wallet</a></p>
      </section>

      {stage === "result" && account && (
        <AccountResult account={account}>
          <p id="vault-saved" hidden={!saved}>Your wallet is saved on this device, encrypted with your password. <a href="/wallet.html">Open your wallet</a></p>
          <p id="vault-failed" className="warning" hidden={saved !== false}>This browser didn't let us save the wallet on this device (private mode?). Payments still work, but you'll need your recovery phrase to use the wallet here.</p>
        </AccountResult>
      )}
    </Shell>
  );
}

mount(<Signup />);
