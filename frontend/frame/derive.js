// Runs only inside the sandboxed, offline iframe (derive.html). The recovery phrase never leaves
// this document in plain text: the parent page only ever gets the account xpubs and the vault
// (the phrase encrypted with the merchant's password), which it stores on this device.
// Docs: docs/security.md#wallet-vault
(function () {
    "use strict";

    // jsbip39 (from Ian Coleman's tool) reads PBKDF2 rounds from that tool's page through jQuery.
    // Give it the BIP39 standard instead of shipping jQuery.
    window.$ = function () {
        return { find: function () { return { val: function () { return 2048; } }; } };
    };
    var mnemonic = new Mnemonic("english");

    // Chain ids match the server API (backend/xpubs.py). ETH's xpub also serves BSC.
    var COINS = [
        { chain: "BTC", coin: 0, net: libs.bitcoin.networks.bitcoin },
        { chain: "LTC", coin: 2, net: libs.bitcoin.networks.litecoin },
        { chain: "DOGE", coin: 3, net: libs.bitcoin.networks.dogecoin },
        { chain: "ETH", coin: 60, net: libs.bitcoin.networks.bitcoin },
        { chain: "TRX", coin: 195, net: libs.bitcoin.networks.bitcoin }
    ];

    function $id(id) { return document.getElementById(id); }

    function deriveXpubs(phrase, passphrase, account) {
        var seedHex = mnemonic.toSeed(phrase, passphrase);
        var out = {};
        COINS.forEach(function (c) {
            var path = "m/44'/" + c.coin + "'/" + account + "'";
            out[c.chain] = libs.bitcoin.HDNode.fromSeedHex(seedHex, c.net).derivePath(path).neutered().toBase58();
        });
        return out;
    }

    // The parent is pinned by the server's `frame-ancestors 'self'` header, so "*" can only reach our own page.
    function send(msg) { window.parent.postMessage(msg, "*"); }
    // Our URL keeps the real origin even though the sandbox makes our own origin opaque.
    var PARENT_ORIGIN = new URL(location.href).origin;

    function show(id) {
        ["create", "import", "password", "unlock", "unlocked", "confirm", "working", "done"].forEach(function (s) { $id(s).hidden = s !== id; });
    }

    // --- Vault: the phrase encrypted with the merchant's password ---------------------------------
    // PBKDF2-SHA256 (OWASP 2023 count) -> AES-256-GCM, all WebCrypto: no extra library.
    // v2 follows Gem Wallet's keystore v4: every metadata field is authenticated as AES-GCM
    // additional data, the format is strict (no unknown fields, fixed sizes, capped lengths), and a
    // wrong password, a modified field or a modified ciphertext all fail the same way.
    // v1 (no authenticated metadata) is still read, then re-encrypted as v2.
    var KDF = "PBKDF2-SHA256";
    var KDF_ITERATIONS = 600000;
    var MIN_ITERATIONS = 100000;
    var MAX_ITERATIONS = 10000000;  // a planted vault can't make unlock hang for minutes
    var MAX_DATA_B64 = 4096;        // phrase + passphrase fit easily
    var V2_FIELDS = ["v", "kdf", "iterations", "salt", "iv", "data"];

    function b64(bytes) { return btoa(String.fromCharCode.apply(null, bytes)); } // vaults are < 4 KB
    function unb64(text) { return Uint8Array.from(atob(text), function (c) { return c.charCodeAt(0); }); }

    function unreadable() { return new Error("unreadable vault"); }

    // Canonical, re-serialized from parsed values: field order or formatting can't change it.
    function vaultAad(v) {
        return new TextEncoder().encode(JSON.stringify([v.v, v.kdf, v.iterations, v.salt, v.iv]));
    }

    function checkVault(v) {
        if (!v || typeof v !== "object" || Array.isArray(v)) throw unreadable();
        var keys = Object.keys(v).sort();
        var expected = V2_FIELDS.slice().sort(); // v1 had the same fields, just no authenticated metadata
        if (keys.join() !== expected.join()) throw unreadable();
        if ((v.v !== 1 && v.v !== 2) || v.kdf !== KDF) throw unreadable();
        if (!Number.isInteger(v.iterations) || v.iterations < MIN_ITERATIONS || v.iterations > MAX_ITERATIONS) throw unreadable();
        if ([v.salt, v.iv, v.data].some(function (x) { return typeof x !== "string"; }) || v.data.length > MAX_DATA_B64) throw unreadable();
        try {
            var salt = unb64(v.salt), iv = unb64(v.iv), data = unb64(v.data);
        } catch (e) {
            throw unreadable();
        }
        if (salt.length !== 16 || iv.length !== 12 || data.length < 17) throw unreadable();
        return { salt: salt, iv: iv, data: data };
    }

    async function vaultKey(password, salt, iterations) {
        var base = await crypto.subtle.importKey("raw", new TextEncoder().encode(password), "PBKDF2", false, ["deriveKey"]);
        return crypto.subtle.deriveKey({ name: "PBKDF2", hash: "SHA-256", salt: salt, iterations: iterations },
            base, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
    }

    async function encryptVault(password, secret) {
        var salt = crypto.getRandomValues(new Uint8Array(16));
        var iv = crypto.getRandomValues(new Uint8Array(12));
        var vault = { v: 2, kdf: KDF, iterations: KDF_ITERATIONS, salt: b64(salt), iv: b64(iv) };
        var key = await vaultKey(password, salt, KDF_ITERATIONS);
        var data = await crypto.subtle.encrypt({ name: "AES-GCM", iv: iv, additionalData: vaultAad(vault) }, key,
            new TextEncoder().encode(JSON.stringify(secret)));
        vault.data = b64(new Uint8Array(data));
        return vault;
    }

    async function decryptVault(password, vault) {
        var bytes = checkVault(vault);
        var params = { name: "AES-GCM", iv: bytes.iv };
        if (vault.v === 2) params.additionalData = vaultAad(vault);
        var key = await vaultKey(password, bytes.salt, vault.iterations);
        var plain = await crypto.subtle.decrypt(params, key, bytes.data);  // throws: wrong password or modified
        var secret = JSON.parse(new TextDecoder().decode(plain));
        if (!secret || typeof secret.phrase !== "string" || !mnemonic.check(secret.phrase)) throw unreadable();
        return secret;
    }

    // --- After the phrase step: choose a password, encrypt, hand over xpubs + vault -----------------
    var pending = null; // {phrase, passphrase, account} between the phrase step and the password step

    function finish(phrase, passphrase, account) {
        var fromCreate = created !== null && phrase === created; // which steps to show: 3 for a new phrase, 2 for an import
        $id("p-steps-create").hidden = !fromCreate;
        $id("p-steps-import").hidden = fromCreate;
        pending = { phrase: phrase, passphrase: passphrase, account: account };
        // Best-effort wipe of the phrase inputs; it now lives only in `pending`.
        $id("words").textContent = "";
        $id("i-phrase").value = "";
        $id("i-pass").value = "";
        $id("c-checks").textContent = "";
        created = null;
        show("password");
        $id("p-new").focus();
    }

    $id("p-done").addEventListener("click", async function () {
        var pw = $id("p-new").value, error = "";
        if (pw.length < 8) error = "Use at least 8 characters.";
        else if (pw !== $id("p-again").value) error = "The passwords don't match.";
        $id("p-error").textContent = error;
        if (error) return;

        var button = this;
        button.disabled = true;
        button.textContent = "Encrypting…";
        try {
            var vault = await encryptVault(pw, pending);
            var xpubs = deriveXpubs(pending.phrase, pending.passphrase, pending.account);
            var account = pending.account;
            pending = null;
            $id("p-new").value = $id("p-again").value = "";
            show("done");
            send({ type: "xpubs", xpubs: xpubs, account: account, vault: vault });
        } catch (e) {
            $id("p-error").textContent = "Encryption failed in this browser (" + e.message + "). Try Chrome, Edge, Firefox or Safari.";
            button.disabled = false;
            button.textContent = "Encrypt and create account";
        }
    });

    // --- Unlock a saved vault -----------------------------------------------------------------------
    var LOCK_AFTER_MS = 15 * 60 * 1000;
    var savedVault = null;
    var unlocked = null;  // {phrase, passphrase, account}: kept only in memory, for signing (Send, coming next)
    var lockTimer = null;

    function lock() {
        if (sending && sending.cancel) sending.cancel();
        sending = null;
        unlocked = null;
        clearTimeout(lockTimer);
        show("unlock");
        send({ type: "locked" });
    }

    window.addEventListener("message", function (e) {
        // Only our own parent page may hand us a vault.
        if (e.source !== window.parent || e.origin !== PARENT_ORIGIN || !e.data) return;
        if (e.data.type === "vault" && location.hash === "#unlock" && !savedVault) {
            savedVault = e.data.vault;
            show("unlock");
            $id("u-pass").focus();
        } else if (e.data.type === "sign-login" && unlocked) {
            signLogin(String(e.data.challenge || ""));
        } else if (e.data.type === "engine-files" && engineWaiter) {
            engineWaiter(e.data.files || {});
        } else if (e.data.type === "sign-send" && unlocked && !sending) {
            prepareSend(e.data.request || {}).catch(sendFailed);
        }
    });

    // Dashboard login: sign the server's challenge with the key at m/44'/0'/account'/0/0.
    // Only challenges in the server's exact format are signed, always behind a fixed text prefix,
    // so the page can't use this to get a signature over a transaction.
    var LOGIN_PREFIX = "Gateway login\n";
    var CHALLENGE_FORMAT = /^gw-login:[A-Za-z0-9_-]{20,64}$/;

    function signLogin(challenge) {
        if (!CHALLENGE_FORMAT.test(challenge)) return;
        var seedHex = mnemonic.toSeed(unlocked.phrase, unlocked.passphrase);
        var net = libs.bitcoin.networks.bitcoin;
        var account = libs.bitcoin.HDNode.fromSeedHex(seedHex, net).derivePath("m/44'/0'/" + unlocked.account + "'");
        var key = account.derive(0).derive(0);
        var hash = libs.bitcoin.crypto.sha256(libs.buffer.Buffer.from(LOGIN_PREFIX + challenge, "utf8"));
        send({ type: "login-signature", challenge: challenge, xpub: account.neutered().toBase58(),
               signature: key.sign(hash).toDER().toString("hex") });
    }

    $id("u-done").addEventListener("click", async function () {
        var button = this;
        button.disabled = true;
        $id("u-error").textContent = "";
        try {
            unlocked = await decryptVault($id("u-pass").value, savedVault);
        } catch (e) {
            $id("u-error").textContent = e.message === "unreadable vault"
                ? "This saved wallet can't be read. Remove it from this device and restore it with your recovery phrase."
                : "Wrong password.";
            button.disabled = false;
            return;
        }
        if (savedVault.v === 1) {
            // Upgrade to v2 with the password just typed; the page saves the new copy.
            encryptVault($id("u-pass").value, unlocked).then(function (v2) {
                savedVault = v2;
                send({ type: "vault-upgraded", vault: v2 });
            });
        }
        $id("u-pass").value = "";
        button.disabled = false;
        show("unlocked");
        lockTimer = setTimeout(lock, LOCK_AFTER_MS);
        send({ type: "unlocked", account: unlocked.account });
    });

    // --- Send (Gem-style: the page prepares, this frame plans, shows, signs) ---------------------
    // The signing engine is Trust Wallet Core (WebAssembly). The page hands over its files; they run
    // only if their SHA-256 matches these values, which build_frame.py pins into this hash-locked script.
    var ENGINE_SHA256 = __ENGINE_SHA256__;
    var enginePromise = null;
    var engineWaiter = null;
    var sending = null; // the request being confirmed

    // Our accounts use BIP44 legacy P2PKH addresses. Wallet Core defaults BTC/LTC to segwit,
    // so legacy addresses are built explicitly with each coin's P2PKH prefix.
    var UTXO = {
        BTC: { coin: "bitcoin", bip44: 0, prefix: 0x00, decimals: 8 },
        LTC: { coin: "litecoin", bip44: 2, prefix: 0x30, decimals: 8 },
        DOGE: { coin: "dogecoin", bip44: 3, prefix: 0x1e, decimals: 8 }
    };

    async function sha256hex(buf) {
        var d = new Uint8Array(await crypto.subtle.digest("SHA-256", buf));
        return Array.prototype.map.call(d, function (b) { return b.toString(16).padStart(2, "0"); }).join("");
    }

    function runScript(code) {
        return new Promise(function (resolve, reject) {
            var url = URL.createObjectURL(new Blob([code], { type: "text/javascript" }));
            var el = document.createElement("script");
            el.src = url;
            el.onload = function () { URL.revokeObjectURL(url); resolve(); };
            el.onerror = function () { reject(new Error("engine script failed to run")); };
            document.head.appendChild(el);
        });
    }

    function loadEngine() {
        if (enginePromise) return enginePromise;
        enginePromise = new Promise(function (resolve) {
            engineWaiter = resolve;
            send({ type: "engine-request", files: Object.keys(ENGINE_SHA256) });
        }).then(async function (files) {
            engineWaiter = null;
            for (var name in ENGINE_SHA256) {
                if (!(files[name] instanceof ArrayBuffer) || await sha256hex(files[name]) !== ENGINE_SHA256[name]) {
                    throw new Error("signing engine file " + name + " failed its integrity check");
                }
            }
            var text = function (n) { return new TextDecoder().decode(files[n]); };
            await runScript(text("protobuf.min.js"));
            await runScript("(function(){var module={exports:{}};var require=function(){return window.protobuf;};\n" +
                text("core_proto.js") + "\n;window.__walletCoreProto=module.exports;})();");
            await runScript("(function(){var module={exports:{}},exports=module.exports;\n" +
                text("wallet-core.js") + "\n;window.__walletCoreInit=module.exports;})();");
            // The engine fetches its .wasm; answer from the verified bytes. Nothing else is fetchable.
            var wasm = files["wallet-core.wasm"];
            window.fetch = function (url) {
                if (String(url).split("?")[0].endsWith("wallet-core.wasm")) {
                    return Promise.resolve(new Response(wasm.slice(0), { headers: { "Content-Type": "application/wasm" } }));
                }
                return Promise.reject(new Error("this window is offline"));
            };
            var core = await window.__walletCoreInit();
            return { core: core, TW: window.__walletCoreProto.TW };
        });
        enginePromise.catch(function () { enginePromise = null; });
        return enginePromise;
    }

    function toUnits(text, decimals) {
        var m = /^(\d{1,12})(?:\.(\d{1,8}))?$/.exec(String(text).trim());
        if (!m || (m[2] || "").length > decimals) return null;
        var units = Number(m[1]) * Math.pow(10, decimals) + Number((m[2] || "").padEnd(decimals, "0") || 0);
        return Number.isSafeInteger(units) && units > 0 ? units : null;
    }

    function fromUnits(units, decimals) {
        var s = String(units).padStart(decimals + 1, "0");
        var whole = s.slice(0, -decimals), frac = s.slice(-decimals).replace(/0+$/, "");
        return frac ? whole + "." + frac : whole;
    }

    function working(text) {
        $id("working-text").textContent = text;
        $id("working-error").textContent = "";
        $id("working-back").hidden = true;
        show("working");
    }

    function sendFailed(err) {
        sending = null;
        $id("working-text").textContent = "";
        $id("working-error").textContent = err.message || String(err);
        $id("working-back").hidden = false;
        show("working");
    }

    $id("working-back").addEventListener("click", function () {
        show("unlocked");
        send({ type: "send-cancelled" });
    });

    // request: {id, chain, to, amount | max, fee_per_byte, utxos: [{address, index, tx_hash, vout, value}], change_index}
    async function prepareSend(req) {
        if (EVM[req.chain]) return prepareEvm(req);
        if (TRON[req.chain]) return prepareTron(req);
        var spec = UTXO[req.chain];
        if (!spec) throw new Error("Sending " + req.chain + " isn't supported yet.");
        sending = req;
        clearTimeout(lockTimer);
        lockTimer = setTimeout(lock, LOCK_AFTER_MS);
        working("Loading the signing engine…");
        var eng = await loadEngine();
        var core = eng.core, TW = eng.TW, coin = core.CoinType[spec.coin];
        working("Checking the transaction…");

        var to = String(req.to || "").trim();
        if (!core.AnyAddress.isValid(to, coin)) throw new Error("That isn't a valid " + req.chain + " address.");
        var feePerByte = Number(req.fee_per_byte);
        if (!Number.isSafeInteger(feePerByte) || feePerByte < 1 || feePerByte > 10000000) throw new Error("Bad fee rate from the server.");
        var amount = req.max ? 0 : toUnits(req.amount, spec.decimals);
        if (!req.max && !amount) throw new Error("Enter an amount greater than 0.");

        // Derive the key for every address the server listed, and refuse any address that isn't ours
        // at that index: a wrong list can't trick this window into signing for someone else's coins.
        var wallet = core.HDWallet.createWithMnemonic(unlocked.phrase, unlocked.passphrase || "");
        var base = "m/44'/" + spec.bip44 + "'/" + unlocked.account + "'/0/";
        var keyFor = {}, utxos = [], total = 0;
        function legacy(index) {
            var key = wallet.getKey(coin, base + index);
            var addr = core.BitcoinAddress.createWithPublicKey(key.getPublicKeySecp256k1(true), spec.prefix).description();
            keyFor[addr] = key;
            return addr;
        }
        var change = legacy(Number(req.change_index) || 0);
        (req.utxos || []).forEach(function (u) {
            if (!Number.isInteger(u.index) || u.index < 0 || legacy(u.index) !== u.address) throw new Error("The server listed an address that isn't in this wallet. Nothing was signed.");
            if (!/^[0-9a-f]{64}$/.test(u.tx_hash) || !Number.isInteger(u.vout) || !Number.isSafeInteger(u.value) || u.value <= 0) throw new Error("Bad coin data from the server.");
            total += u.value;
            utxos.push({
                amount: u.value,
                script: core.BitcoinScript.lockScriptForAddress(u.address, coin).data(),
                outPoint: { hash: core.HexCoding.decode(u.tx_hash).reverse(), index: u.vout, sequence: 0xffffffff }
            });
        });
        if (!utxos.length) throw new Error("No confirmed " + req.chain + " to send yet.");

        var keys = Object.keys(keyFor).map(function (a) { return keyFor[a].data(); });
        var input = TW.Bitcoin.Proto.SigningInput.create({
            hashType: 1, amount: req.max ? total : amount, byteFee: feePerByte, toAddress: to,
            changeAddress: change, coinType: coin.value, privateKey: keys, utxo: utxos, useMaxAmount: !!req.max
        });
        var inputBytes = TW.Bitcoin.Proto.SigningInput.encode(input).finish();
        var plan = TW.Bitcoin.Proto.TransactionPlan.decode(core.AnySigner.plan(inputBytes, coin));
        if (plan.error) throw new Error(plan.error === TW.Common.Proto.SigningError.Error_not_enough_utxos || plan.error === TW.Common.Proto.SigningError.Error_missing_input_utxos
            ? "Not enough " + req.chain + " to cover the amount and the network fee."
            : "Can't build this transaction (" + (plan.errorMessage || "error " + plan.error) + ").");

        var sendAmount = Number(plan.amount), fee = Number(plan.fee);
        $id("cf-amount").textContent = fromUnits(sendAmount, spec.decimals) + " " + req.chain;
        $id("cf-to").textContent = to;
        $id("cf-fee").textContent = fromUnits(fee, spec.decimals) + " " + req.chain + " (" + feePerByte + " per byte)";
        $id("cf-total").textContent = fromUnits(sendAmount + fee, spec.decimals) + " " + req.chain;
        sending.sign = function () {
            var out = TW.Bitcoin.Proto.SigningOutput.decode(core.AnySigner.sign(inputBytes, coin));
            wallet.delete();
            if (out.error) throw new Error("Signing failed (" + (out.errorMessage || out.error) + ").");
            return { txs: [core.HexCoding.encode(out.encoded).replace(/^0x/, "")], symbol: req.chain,
                     amount: fromUnits(sendAmount, spec.decimals), fee: fromUnits(fee, spec.decimals) + " " + req.chain };
        };
        sending.cancel = function () { wallet.delete(); };
        show("confirm");
    }

    // --- EVM and Tron (native coins and USDT) ------------------------------------------------------
    // Chain ids, token contracts and decimals are pinned here, never taken from the server: they decide
    // which network a signature is valid on and what a token amount means.
    var EVM = {
        ETH: { chainId: 1, symbol: "ETH", name: "Ethereum" },
        BSC: { chainId: 56, symbol: "BNB", name: "BNB Smart Chain" },
        SEPOLIA: { chainId: 11155111, symbol: "ETH", name: "Sepolia testnet (no real value)" }
    };
    var TRON = {
        TRX: { name: "Tron" },
        NILE: { name: "Tron Nile testnet (no real value)" }
    };
    var TOKENS = {
        "ETH/USDT": { contract: "0xdAC17F958D2ee523a2206206994597C13D831ec7", decimals: 6 },
        "BSC/USDT": { contract: "0x55d398326f99059fF775485246999027B3197955", decimals: 18 },
        "SEPOLIA/USDT": { contract: "0xaA8E23Fb1079EA71e0a56F48a2aA51851D8433D0", decimals: 6 },
        "TRX/USDT": { contract: "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t", decimals: 6 },
        "NILE/USDT": { contract: "TXYZopYRdj2D9XRtbG411XZZ3kM5VkAeBf", decimals: 6 }
    };
    var GAS_LIMIT = 21000;            // plain transfer (and every gas top-up)
    var MAX_TOKEN_GAS = 200000;       // a USDT transfer uses ~35-65k; more means a bad estimate
    var MAX_GAS_PRICE = 10n ** 13n;   // 10,000 gwei: a sane ceiling against a bad fee quote
    var TRON_TX_BYTES = 300n;         // bandwidth of a TRX transfer (~270), rounded up
    var TRON_TOKEN_BYTES = 350n;      // bandwidth of a TRC20 transfer (~345)
    var MAX_TRON_FEE_LIMIT = 100000000n; // 100 TRX: a USDT transfer burns at most ~15 today
    var TRON_EXPIRY_MS = 20 * 60 * 1000; // tokens may wait a few minutes for their gas top-up

    function toBig(text, decimals) {
        var m = /^(\d{1,15})(?:\.(\d{1,18}))?$/.exec(String(text).trim());
        if (!m || (m[2] || "").length > decimals) return null;
        var units = BigInt(m[1] + (m[2] || "").padEnd(decimals, "0"));
        return units > 0n ? units : null;
    }

    function bigFrom(text) {
        if (!/^\d{1,40}$/.test(String(text))) throw new Error("Bad number from the server.");
        return BigInt(text);
    }

    function small(n, max) {
        if (!Number.isSafeInteger(n) || n < 0 || n > max) throw new Error("Bad number from the server.");
        return BigInt(n);
    }

    async function begin(req) {
        sending = req;
        clearTimeout(lockTimer);
        lockTimer = setTimeout(lock, LOCK_AFTER_MS);
        working("Loading the signing engine…");
        var eng = await loadEngine();
        working("Checking the transaction…");
        return eng;
    }

    // Derive the key for every address the server listed and refuse any that isn't ours at that index.
    function ownKeys(core, coin, bip44, list) {
        var wallet = core.HDWallet.createWithMnemonic(unlocked.phrase, unlocked.passphrase || "");
        var base = "m/44'/" + bip44 + "'/" + unlocked.account + "'/0/";
        var out = list.map(function (s) {
            if (!s || !Number.isInteger(s.index) || s.index < 0) throw new Error("Bad account data from the server.");
            var key = wallet.getKey(coin, base + s.index);
            var addr = core.AnyAddress.createWithPublicKey(key.getPublicKeySecp256k1(false), coin).description();
            if (addr.toLowerCase() !== String(s.address).toLowerCase()) throw new Error("The server listed an address that isn't in this wallet. Nothing was signed.");
            return Object.assign({}, s, { key: key, address: addr });
        });
        return { wallet: wallet, list: out };
    }

    // Largest first, so as few addresses (and fees) as possible are used. avail(s) = what s can send.
    function pick(sources, amount, max, avail) {
        var plan = [], remaining = amount;
        sources.slice().sort(function (a, b) { var x = avail(a), y = avail(b); return x > y ? -1 : x < y ? 1 : 0; })
            .forEach(function (s) {
                var can = avail(s);
                if (can <= 0n || (!max && remaining <= 0n)) return;
                var value = max ? can : (can < remaining ? can : remaining);
                remaining -= value;
                plan.push({ source: s, value: value });
            });
        return { plan: plan, short: max ? 0n : remaining, total: plan.reduce(function (t, p) { return t + p.value; }, 0n) };
    }

    function confirmScreen(amountText, to, feeText, totalText) {
        $id("cf-amount").textContent = amountText;
        $id("cf-to").textContent = to;
        $id("cf-fee").textContent = feeText;
        $id("cf-total").textContent = totalText;
        show("confirm");
    }

    async function prepareEvm(req) {
        var spec = EVM[req.chain], token = TOKENS[req.chain + "/" + req.asset];
        if (req.asset && req.asset !== spec.symbol && !token) throw new Error("Sending " + req.asset + " on " + spec.name + " isn't supported.");
        var eng = await begin(req), core = eng.core, TW = eng.TW, coin = core.CoinType.ethereum;

        var to = String(req.to || "").trim();
        if (!core.AnyAddress.isValid(to, coin)) throw new Error("That isn't a valid " + spec.name + " address (0x…).");
        if (Number(req.gas_limit) !== GAS_LIMIT) throw new Error("Unexpected gas limit from the server.");
        var fee = req.fee || {}, legacyFee = fee.gas_price !== undefined;
        var price = bigFrom(legacyFee ? fee.gas_price : fee.max_fee_per_gas);
        var tip = legacyFee ? 0n : bigFrom(fee.max_priority_fee_per_gas);
        if (price <= 0n || price > MAX_GAS_PRICE || tip > price) throw new Error("Bad fee quote from the server.");
        var feeEach = price * BigInt(GAS_LIMIT); // the most one plain transfer can cost
        var decimals = token ? token.decimals : 18, symbol = token ? req.asset : spec.symbol;
        var amount = req.max ? 0n : toBig(req.amount, decimals);
        if (!req.max && !amount) throw new Error("Enter an amount greater than 0.");

        var keys = ownKeys(core, coin, 60, (token ? [req.main] : []).concat(req.sources || []));
        var main = token ? keys.list.shift() : null;
        if (main && main.index !== 0) throw new Error("Bad account data from the server.");
        var sources = keys.list.map(function (s) {
            return Object.assign(s, { nonce: small(s.nonce, 1e9), balance: bigFrom(s.balance),
                                      token: token ? bigFrom(s.token_balance) : 0n,
                                      gas: token ? small(s.token_gas, MAX_TOKEN_GAS) * 5n / 4n : 0n }); // 25% margin
        });
        if (main) Object.assign(main, { nonce: small(main.nonce, 1e9), balance: bigFrom(main.balance) });

        var bytes = function (n) { var h = n.toString(16); return core.HexCoding.decode(h.length % 2 ? "0" + h : h); };
        function sign(key, nonce, gasLimit, toAddress, transaction) {
            var input = TW.Ethereum.Proto.SigningInput.create({
                chainId: bytes(BigInt(spec.chainId)), nonce: bytes(nonce), gasLimit: bytes(gasLimit),
                txMode: legacyFee ? TW.Ethereum.Proto.TransactionMode.Legacy : TW.Ethereum.Proto.TransactionMode.Enveloped,
                gasPrice: legacyFee ? bytes(price) : undefined,
                maxFeePerGas: legacyFee ? undefined : bytes(price),
                maxInclusionFeePerGas: legacyFee ? undefined : bytes(tip),
                toAddress: toAddress, privateKey: key.data(), transaction: transaction
            });
            var out = TW.Ethereum.Proto.SigningOutput.decode(core.AnySigner.sign(TW.Ethereum.Proto.SigningInput.encode(input).finish(), coin));
            if (out.error) throw new Error("Signing failed (" + (out.errorMessage || out.error) + ").");
            return core.HexCoding.encode(out.encoded).replace(/^0x/, "");
        }

        if (!token) { // each address with a balance sends its own transfer and pays its own fee
            var picked = pick(sources, amount, req.max, function (s) { return s.balance - feeEach; });
            if (!picked.plan.length) throw new Error("No " + symbol + " to send: each address needs more than its network fee (" + fromUnits(feeEach, 18) + " " + symbol + ").");
            if (picked.short > 0n) throw new Error("Not enough " + symbol + ". Each address pays its own network fee, so at most " +
                fromUnits(amount - picked.short, 18) + " " + symbol + " can be sent right now.");
            var maxFees = feeEach * BigInt(picked.plan.length);
            sending.sign = function () {
                var txs = picked.plan.map(function (p) {
                    return sign(p.source.key, p.source.nonce, BigInt(GAS_LIMIT), to, { transfer: { amount: bytes(p.value) } });
                });
                keys.wallet.delete();
                return { txs: txs, topups: [], symbol: symbol, amount: fromUnits(picked.total, 18), fee: "up to " + fromUnits(maxFees, 18) + " " + symbol };
            };
            sending.cancel = function () { keys.wallet.delete(); };
            return confirmScreen(fromUnits(picked.total, 18) + " " + symbol + " on " + spec.name, to,
                "up to " + fromUnits(maxFees, 18) + " " + symbol + (picked.plan.length > 1 ? " (" + picked.plan.length + " transfers, one from each address)" : ""),
                "up to " + fromUnits(picked.total + maxFees, 18) + " " + symbol);
        }

        // USDT: the main address pays gas. Payment addresses without enough get a top-up from it first.
        var plan = pick(sources, amount, req.max, function (s) { return s.token; });
        if (!plan.plan.length) throw new Error("No " + symbol + " to send.");
        if (plan.short > 0n) throw new Error("Not enough " + symbol + ": at most " + fromUnits(amount - plan.short, decimals) + " " + symbol + " can be sent.");
        var topups = [], mainNeeds = 0n, gasTotal = 0n;
        plan.plan.forEach(function (p) {
            var cost = p.source.gas * price;
            gasTotal += cost;
            if (p.source.index === 0) { mainNeeds += cost; return; }
            if (p.source.balance < cost) { topups.push({ to: p.source.address, value: cost - p.source.balance }); mainNeeds += cost - p.source.balance + feeEach; gasTotal += feeEach; }
        });
        if (mainNeeds > main.balance) throw new Error("Your main address pays the network fees and needs up to " + fromUnits(mainNeeds, 18) + " " + spec.symbol +
            ", but has " + fromUnits(main.balance, 18) + ". Use Receive to add " + fromUnits(mainNeeds - main.balance, 18) + " " + spec.symbol + " to it, then try again.");
        sending.sign = function () {
            var nonce = main.nonce;
            var signedTopups = topups.map(function (t) { return sign(main.key, nonce++, BigInt(GAS_LIMIT), t.to, { transfer: { amount: bytes(t.value) } }); });
            var txs = plan.plan.map(function (p) {
                var n = p.source.index === 0 ? nonce++ : p.source.nonce; // main's own transfer comes after its top-ups
                return sign(p.source.key, n, p.source.gas, token.contract, { erc20Transfer: { to: to, amount: bytes(p.value) } });
            });
            keys.wallet.delete();
            return { txs: txs, topups: signedTopups, symbol: symbol, amount: fromUnits(plan.total, decimals), fee: "up to " + fromUnits(gasTotal, 18) + " " + spec.symbol };
        };
        sending.cancel = function () { keys.wallet.delete(); };
        confirmScreen(fromUnits(plan.total, decimals) + " " + symbol + " on " + spec.name, to,
            "up to " + fromUnits(gasTotal, 18) + " " + spec.symbol + ", paid from your main address" +
                (topups.length ? " (" + topups.length + " gas top-up" + (topups.length > 1 ? "s" : "") + " to payment addresses, sent first)" : ""),
            fromUnits(plan.total, decimals) + " " + symbol + " + up to " + fromUnits(gasTotal, 18) + " " + spec.symbol);
    }

    // Tron: amounts in sun (1 TRX = 1,000,000). Fees are burned bandwidth/energy, and sending TRX to an
    // address that never had any activates it for a fixed fee.
    async function prepareTron(req) {
        var spec = TRON[req.chain], token = TOKENS[req.chain + "/" + req.asset];
        if (req.asset && req.asset !== "TRX" && !token) throw new Error("Sending " + req.asset + " on " + spec.name + " isn't supported.");
        var eng = await begin(req), core = eng.core, TW = eng.TW, coin = core.CoinType.tron;

        var to = String(req.to || "").trim();
        if (!core.AnyAddress.isValid(to, coin)) throw new Error("That isn't a valid " + spec.name + " address (T…).");
        var p = req.params || {}, b = req.block || {};
        var bwFee = small(p.bandwidth_fee, 100000), energyFee = small(p.energy_fee, 100000), createFee = small(p.create_fee, 10000000);
        if (!/^[0-9a-f]{64}$/.test(b.tx_trie_root) || !/^[0-9a-f]{64}$/.test(b.parent_hash) || !/^41[0-9a-f]{40}$/.test(b.witness_address)) throw new Error("Bad block data from the server.");
        var header = { timestamp: Number(small(b.timestamp, 1e14)), number: Number(small(b.number, 1e12)), version: Number(small(b.version, 1000)),
                       txTrieRoot: core.HexCoding.decode(b.tx_trie_root), parentHash: core.HexCoding.decode(b.parent_hash),
                       witnessAddress: core.HexCoding.decode(b.witness_address) };
        var decimals = token ? token.decimals : 6, symbol = token ? req.asset : "TRX";
        var amount = req.max ? 0n : toBig(req.amount, decimals);
        if (!req.max && !amount) throw new Error("Enter an amount greater than 0.");

        var keys = ownKeys(core, coin, 195, (token ? [req.main] : []).concat(req.sources || []));
        var main = token ? keys.list.shift() : null;
        if (main && main.index !== 0) throw new Error("Bad account data from the server.");
        function amounts(s) {
            return Object.assign(s, { balance: bigFrom(s.balance), bandwidth: small(s.bandwidth, 1e12), energy: small(s.energy, 1e15), active: !!s.active });
        }
        var sources = keys.list.map(amounts);
        if (main) amounts(main);

        var now = Date.now();
        function sign(key, transaction) {
            var input = TW.Tron.Proto.SigningInput.create({
                transaction: Object.assign({ timestamp: now, expiration: now + TRON_EXPIRY_MS, blockHeader: header }, transaction),
                privateKey: key.data()
            });
            var out = TW.Tron.Proto.SigningOutput.decode(core.AnySigner.sign(TW.Tron.Proto.SigningInput.encode(input).finish(), coin));
            if (out.error) throw new Error("Signing failed (" + (out.errorMessage || out.error) + ").");
            // Relay format: the Transaction protobuf = raw_data (field 1) + signature (field 2).
            var raw = JSON.parse(out.json).raw_data_hex, sig = core.HexCoding.encode(out.signature).replace(/^0x/, "");
            var varint = function (n) { var s = ""; while (n > 127) { s += ((n & 127) | 128).toString(16).padStart(2, "0"); n >>>= 7; } return s + n.toString(16).padStart(2, "0"); };
            return "0a" + varint(raw.length / 2) + raw + "12" + varint(sig.length / 2) + sig;
        }
        var trxFee = function (s) { return !req.to_active ? createFee : s.bandwidth >= TRON_TX_BYTES ? 0n : TRON_TX_BYTES * bwFee; };

        if (!token) {
            var picked = pick(sources, amount, req.max, function (s) { return s.balance - trxFee(s); });
            if (!picked.plan.length) throw new Error("No TRX to send after network fees.");
            if (picked.short > 0n) throw new Error("Not enough TRX. Each address pays its own network fee, so at most " + fromUnits(amount - picked.short, 6) + " TRX can be sent right now.");
            var maxFees = picked.plan.reduce(function (t, x) { return t + trxFee(x.source); }, 0n);
            sending.sign = function () {
                var txs = picked.plan.map(function (x) {
                    return sign(x.source.key, { transfer: { ownerAddress: x.source.address, toAddress: to, amount: Number(x.value) } });
                });
                keys.wallet.delete();
                return { txs: txs, topups: [], symbol: "TRX", amount: fromUnits(picked.total, 6), fee: "up to " + fromUnits(maxFees, 6) + " TRX" };
            };
            sending.cancel = function () { keys.wallet.delete(); };
            return confirmScreen(fromUnits(picked.total, 6) + " TRX on " + spec.name, to,
                "up to " + fromUnits(maxFees, 6) + " TRX" + (req.to_active ? "" : " (includes activating this new address)") +
                    (picked.plan.length > 1 ? " (" + picked.plan.length + " transfers, one from each address)" : ""),
                "up to " + fromUnits(picked.total + maxFees, 6) + " TRX");
        }

        // USDT (TRC20): burned energy + bandwidth, paid in TRX. The main address tops up the rest.
        sources.forEach(function (s) {
            s.burnEnergy = small(s.token_energy, 1e7) * 6n / 5n; // 20% margin
            s.feeLimit = s.burnEnergy * energyFee;
            if (s.feeLimit > MAX_TRON_FEE_LIMIT) throw new Error("Bad fee estimate from the server.");
            s.cost = (s.burnEnergy > s.energy ? (s.burnEnergy - s.energy) * energyFee : 0n) + (s.bandwidth >= TRON_TOKEN_BYTES ? 0n : TRON_TOKEN_BYTES * bwFee);
            s.token = bigFrom(s.token_balance);
        });
        var plan = pick(sources, amount, req.max, function (s) { return s.token; });
        if (!plan.plan.length) throw new Error("No " + symbol + " to send.");
        if (plan.short > 0n) throw new Error("Not enough " + symbol + ": at most " + fromUnits(amount - plan.short, decimals) + " " + symbol + " can be sent.");
        var topups = [], mainNeeds = 0n, feeTotal = 0n;
        plan.plan.forEach(function (x) {
            var s = x.source;
            feeTotal += s.cost;
            if (s.index === 0) { mainNeeds += s.cost; return; }
            if (s.balance < s.cost || !s.active) {
                var value = s.cost > s.balance ? s.cost - s.balance : 1n; // any TRX activates an address
                var topupFee = s.active ? (main.bandwidth >= TRON_TX_BYTES * BigInt(topups.length + 1) ? 0n : TRON_TX_BYTES * bwFee) : createFee;
                topups.push({ to: s.address, value: value });
                mainNeeds += value + topupFee;
                feeTotal += topupFee;
            }
        });
        if (mainNeeds > main.balance) throw new Error("Your main address pays the network fees and needs up to " + fromUnits(mainNeeds, 6) + " TRX" +
            ", but has " + fromUnits(main.balance, 6) + ". Use Receive to add " + fromUnits(mainNeeds - main.balance, 6) + " TRX to it, then try again.");
        sending.sign = function () {
            var signedTopups = topups.map(function (t) {
                return sign(main.key, { transfer: { ownerAddress: main.address, toAddress: t.to, amount: Number(t.value) } });
            });
            var bytes = function (n) { var h = n.toString(16); return core.HexCoding.decode(h.length % 2 ? "0" + h : h); };
            var txs = plan.plan.map(function (x) {
                return sign(x.source.key, { feeLimit: Number(x.source.feeLimit), transferTrc20Contract: {
                    contractAddress: token.contract, ownerAddress: x.source.address, toAddress: to, amount: bytes(x.value) } });
            });
            keys.wallet.delete();
            return { txs: txs, topups: signedTopups, symbol: symbol, amount: fromUnits(plan.total, decimals), fee: "up to " + fromUnits(feeTotal, 6) + " TRX" };
        };
        sending.cancel = function () { keys.wallet.delete(); };
        confirmScreen(fromUnits(plan.total, decimals) + " " + symbol + " on " + spec.name, to,
            "up to " + fromUnits(feeTotal, 6) + " TRX, paid from your main address" +
                (topups.length ? " (" + topups.length + " gas top-up" + (topups.length > 1 ? "s" : "") + " to payment addresses, sent first)" : ""),
            fromUnits(plan.total, decimals) + " " + symbol + " + up to " + fromUnits(feeTotal, 6) + " TRX");
    }

    $id("cf-ok").addEventListener("click", function () {
        if (!sending || !sending.sign) return;
        var req = sending;
        try {
            var signed = req.sign();
            sending = null;
            show("unlocked");
            send({ type: "send-signed", id: req.id, chain: req.chain, to: String(req.to).trim(), txs: signed.txs, topups: signed.topups || [],
                   symbol: signed.symbol, amount: signed.amount, fee: signed.fee });
        } catch (e) {
            sendFailed(e);
        }
    });

    $id("cf-cancel").addEventListener("click", function () {
        if (sending && sending.cancel) sending.cancel();
        sending = null;
        show("unlocked");
        send({ type: "send-cancelled" });
    });

    // Enter submits the visible step.
    [["p-again", "p-done"], ["u-pass", "u-done"]].forEach(function (pair) {
        $id(pair[0]).addEventListener("keydown", function (e) { if (e.key === "Enter") $id(pair[1]).click(); });
    });

    function normalizePhrase(text) {
        return text.toLowerCase().trim().split(/\s+/).join(" ");
    }

    function randomPositions(count, max) {
        var picked = [];
        var buf = new Uint32Array(1);
        while (picked.length < count) {
            crypto.getRandomValues(buf);
            var n = buf[0] % max;
            if (picked.indexOf(n) < 0) picked.push(n);
        }
        return picked.sort(function (a, b) { return a - b; });
    }

    // --- Create a new wallet --------------------------------------------------------------------
    var created = null;

    function startCreate() {
        created = mnemonic.generate(128); // 12 words, crypto.getRandomValues
        var list = $id("words");
        list.textContent = "";
        created.split(" ").forEach(function (w) {
            var li = document.createElement("li");
            li.textContent = w;
            list.appendChild(li);
        });
        $id("create").hidden = false;
    }

    $id("c-ack").addEventListener("change", function () { $id("c-next").disabled = !this.checked; });

    $id("c-next").addEventListener("click", function () {
        var box = $id("c-checks");
        box.textContent = "";
        randomPositions(3, 12).forEach(function (pos) {
            var label = document.createElement("label");
            label.textContent = "Word #" + (pos + 1);
            var input = document.createElement("input");
            input.dataset.pos = pos;
            input.autocomplete = "off";
            input.spellcheck = false;
            input.setAttribute("autocapitalize", "off");
            label.appendChild(input);
            box.appendChild(label);
        });
        $id("c-error").textContent = "";
        $id("c-show").hidden = true;
        $id("c-confirm").hidden = false;
        box.querySelector("input").focus();
    });

    $id("c-back").addEventListener("click", function () {
        $id("c-confirm").hidden = true;
        $id("c-show").hidden = false;
    });

    $id("c-done").addEventListener("click", function () {
        var words = created.split(" ");
        var wrong = Array.prototype.some.call($id("c-checks").querySelectorAll("input"), function (input) {
            return normalizePhrase(input.value) !== words[Number(input.dataset.pos)];
        });
        if (wrong) {
            $id("c-error").textContent = "Those words don't match. Check your written phrase, or go back and look again.";
            return;
        }
        // New wallets are dedicated to payments, so account 0 (visible in every wallet) is fine.
        finish(created, "", 0);
    });

    // --- Import an existing phrase ----------------------------------------------------------------
    $id("i-done").addEventListener("click", function () {
        var phrase = normalizePhrase($id("i-phrase").value);
        var account = Number($id("i-account").value);
        var error = "";
        if (!phrase) error = "Enter your recovery phrase.";
        else if (!mnemonic.check(phrase)) error = "That isn't a valid recovery phrase. Check the spelling and word order.";
        else if (!Number.isInteger(account) || account < 0 || account > 0x7fffffff) error = "Account number must be 0 or higher.";
        $id("i-error").textContent = error;
        if (!error) finish(phrase, $id("i-pass").value, account);
    });

    // --- Boot -----------------------------------------------------------------------------------
    if (!(window.crypto && crypto.subtle)) {
        send({ type: "frame-error", message: "this browser has no WebCrypto (needs HTTPS)" });
    } else if (location.hash === "#unlock") {
        send({ type: "ready" }); // the parent answers with {type: "vault"}
    } else if (location.hash === "#create") {
        startCreate();
    } else {
        $id("import").hidden = false;
    }

    // Let the parent size the iframe to our content.
    new ResizeObserver(function () {
        send({ type: "height", height: Math.ceil(document.body.getBoundingClientRect().height) }); // content height: documentElement never shrinks below the frame
    }).observe(document.body);
})();
