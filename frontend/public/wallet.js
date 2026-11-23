// Wallet page. Holds only the password-encrypted vault (saved by app.js at signup) and hands it to
// the sandboxed, offline frame, which asks for the password and keeps the unlocked keys.
// After unlock the frame signs a login challenge, and the dashboard uses the resulting session.
// This page can reach our server, so it must never see the password or the phrase.
// Docs: docs/security.md#wallet-vault, docs/api.md#wallet
(function () {
    "use strict";

    var VAULT_KEY = "wallet.vault.v1"; // written by app.js
    var FRAME_TIMEOUT_MS = 12000;       // same as app.js: the inlined engine is ~3.6 MB
    var NETWORKS = { // [what it is, which network]
        "BTC/BTC": ["BTC", "Bitcoin"],
        "LTC/LTC": ["LTC", "Litecoin"],
        "DOGE/DOGE": ["DOGE", "Dogecoin"],
        "ETH/ETH": ["ETH", "Ethereum"],
        "ETH/USDT": ["USDT", "Ethereum (ERC-20)"],
        "BSC/BNB": ["BNB", "BNB Smart Chain (BEP-20)"],
        "BSC/USDT": ["USDT", "BNB Smart Chain (BEP-20)"],
        "TRX/TRX": ["TRX", "Tron"],
        "TRX/USDT": ["USDT", "Tron (TRC-20)"],
        "SEPOLIA/ETH": ["ETH", "Sepolia testnet (no real value)"]
    };
    // Chain -> its native asset, for the assets that can be sent so far (docs/roadmap.md).
    var SENDABLE = { BTC: "BTC", LTC: "LTC", DOGE: "DOGE", ETH: "ETH", BSC: "BNB", SEPOLIA: "ETH" };
    var EXPLORER = {
        BTC: "https://blockchair.com/bitcoin/transaction/",
        LTC: "https://blockchair.com/litecoin/transaction/",
        DOGE: "https://blockchair.com/dogecoin/transaction/",
        ETH: "https://etherscan.io/tx/",
        BSC: "https://bscscan.com/tx/",
        SEPOLIA: "https://sepolia.etherscan.io/tx/"
    };
    var STATUS = { pending: "Waiting", confirming: "Confirming", confirmed: "Paid", expired: "Expired" };

    function $id(id) { return document.getElementById(id); }

    function showError(msg) {
        $id("error").textContent = msg || "";
        $id("error").hidden = !msg;
    }

    function load() {
        try { return JSON.parse(localStorage.getItem(VAULT_KEY)); } catch (e) { return null; }
    }

    var saved = load();
    var frame = $id("frame");
    var timer = null;
    var session = null; // "gws_..." token, memory only: lock or reload = sign in again

    if (!saved || !saved.vault) {
        $id("none").hidden = false;
        return;
    }

    // --- API --------------------------------------------------------------------------------------
    async function api(method, path, body) {
        var headers = { "Content-Type": "application/json" };
        if (session) headers.Authorization = "Bearer " + session;
        var res = await fetch(path, { method: method, headers: headers, body: body ? JSON.stringify(body) : undefined });
        var data = res.status === 204 ? {} : await res.json().catch(function () { return {}; });
        if (!res.ok) throw new Error((data.detail && data.detail.error) || "HTTP " + res.status);
        return data;
    }

    // --- Lock / unlock ----------------------------------------------------------------------------
    // Locking = reloading the frame (everything it had in memory is gone) and ending the session.
    function startFrame() {
        if (session) api("DELETE", "/auth/session").catch(function () {});
        session = null;
        $id("dashboard").hidden = true;
        $id("content").hidden = true;
        $id("receive").hidden = true;
        $id("lock").hidden = true;
        $id("vault").hidden = false;
        $id("frame-status").hidden = false;
        frame.hidden = false;
        showError("");
        clearTimeout(timer);
        timer = setTimeout(function () {
            $id("frame-status").hidden = true;
            frame.hidden = true;
            frame.src = "about:blank";
            showError("Your browser blocked the secure wallet window, so it can't run here. Open this page in Chrome, Edge, Firefox or Safari.");
        }, FRAME_TIMEOUT_MS);
        frame.src = "derive.html#unlock";
    }

    async function signIn() {
        $id("vault").hidden = true; // the frame stays loaded, just out of sight
        $id("dashboard").hidden = false;
        $id("signing-in").hidden = false;
        $id("lock").hidden = false;
        try {
            var c = await api("POST", "/auth/challenge");
            frame.contentWindow.postMessage({ type: "sign-login", challenge: c.challenge }, "*");
        } catch (e) {
            showError("Couldn't reach the server (" + e.message + "). Lock and try again.");
        }
    }

    async function finishSignIn(msg) {
        try {
            var r = await api("POST", "/auth/login", { xpub: msg.xpub, challenge: msg.challenge, signature: msg.signature });
            session = r.token;
        } catch (e) {
            $id("signing-in").hidden = true;
            showError(e.message === "no account for this wallet"
                ? "This wallet has no account with us yet. Set it up again from the start page with the same recovery phrase."
                : "Sign-in failed (" + e.message + "). Lock and try again.");
            return;
        }
        $id("signing-in").hidden = true;
        $id("content").hidden = false;
        loadDashboard();
    }

    window.addEventListener("message", function (e) {
        if (e.source !== frame.contentWindow || !e.data) return;
        clearTimeout(timer);
        $id("frame-status").hidden = true;
        var type = e.data.type;
        if (type === "ready") {
            // The frame's origin is opaque ("null"), so "*" is the only target; it's our own frame (src above).
            frame.contentWindow.postMessage({ type: "vault", vault: saved.vault }, "*");
        } else if (type === "height") {
            frame.style.height = Math.min(Number(e.data.height) || 0, 2000) + 24 + "px";
        } else if (type === "unlocked") {
            signIn();
        } else if (type === "vault-upgraded" && e.data.vault) {
            saved.vault = e.data.vault;  // still only ciphertext
            try { localStorage.setItem(VAULT_KEY, JSON.stringify(saved)); } catch (err) { /* keep using v1 */ }
        } else if (type === "login-signature") {
            finishSignIn(e.data);
        } else if (type === "engine-request") {
            sendEngine(e.data.files);
        } else if (type === "send-signed") {
            broadcastSigned(e.data);
        } else if (type === "send-cancelled") {
            hideSigner();
            $id("send-review").disabled = false;
        } else if (type === "locked") {
            startFrame(); // auto-lock after 15 minutes
        } else if (type === "frame-error") {
            showError("The secure wallet window failed to load (" + String(e.data.message).slice(0, 200) + "). Please reload.");
        }
    });

    // --- Dashboard --------------------------------------------------------------------------------
    function label(chain, asset) { return NETWORKS[chain + "/" + asset] || [asset, chain]; }

    function el(tag, className, text) {
        var node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function loadDashboard() {
        loadBalances();
        loadPayments();
    }

    async function loadBalances() {
        var list = $id("balances");
        list.textContent = "";
        list.appendChild(el("li", "note", "Checking the blockchains…"));
        var data;
        try {
            data = await api("GET", "/wallet/balances");
        } catch (e) {
            list.textContent = "";
            return showError("Couldn't load balances (" + e.message + ").");
        }
        list.textContent = "";
        fillChargeNetworks(data.balances);
        var incomplete = false;
        data.balances.forEach(function (b) {
            var name = label(b.chain, b.asset);
            var li = el("li");
            var left = el("div");
            left.append(el("div", "asset", name[0]), el("div", "network", name[1]));
            var amount = el("div", "amount", (b.complete ? "" : "≥ ") + b.balance + " " + b.asset);
            var buttons = el("div", "actions-cell");
            var button = el("button", "secondary", "Receive");
            button.type = "button";
            button.setAttribute("aria-label", "Receive " + name[0] + " on " + name[1]);
            button.addEventListener("click", function () { showReceive(b); });
            buttons.appendChild(button);
            if (SENDABLE[b.chain] === b.asset) {
                var sendButton = el("button", "secondary", "Send");
                sendButton.type = "button";
                sendButton.setAttribute("aria-label", "Send " + name[0]);
                sendButton.addEventListener("click", function () { showSend(b); });
                buttons.appendChild(sendButton);
            }
            li.append(left, amount, buttons);
            list.appendChild(li);
            if (!b.complete) incomplete = true;
        });
        $id("balances-note").hidden = !incomplete;
    }

    function showReceive(b) {
        var name = label(b.chain, b.asset);
        $id("receive-network").textContent = name[1];
        $id("receive-address").value = b.main_address;
        $id("receive-warning").textContent = "Send only " + name[0] + " on " + name[1] + " to this address. Other coins, tokens or networks can be lost.";
        $id("receive-qr").replaceChildren(kjua({ text: b.main_address, render: "canvas", size: 200, ecLevel: "M", fill: "#000", back: "#fff" }));
        $id("receive").hidden = false;
        $id("receive").scrollIntoView({ block: "nearest" });
    }

    async function loadPayments() {
        var data;
        try {
            data = await api("GET", "/payments?limit=20");
        } catch (e) {
            return showError("Couldn't load payments (" + e.message + ").");
        }
        var body = $id("payments").querySelector("tbody");
        body.textContent = "";
        data.payments.forEach(function (p) {
            var tr = el("tr");
            var created = el("td");
            var link = el("a", "", p.created_at.replace("T", " ").replace("Z", " UTC"));
            link.href = "/pay/" + encodeURIComponent(p.id);
            created.appendChild(link);
            var name = label(p.chain, p.asset);
            tr.append(created, el("td", "num", p.amount + " " + name[0] + " on " + name[1]),
                      el("td", "", STATUS[p.status] || p.status));
            body.appendChild(tr);
        });
        $id("payments").hidden = !data.payments.length;
        $id("payments-empty").hidden = !!data.payments.length;
    }

    // --- New payment: what a shop does with POST /payments, here with the wallet session ---------------
    function fillChargeNetworks(balances) {
        var select = $id("charge-network"), current = select.value;
        select.textContent = "";
        balances.forEach(function (b) {
            var name = label(b.chain, b.asset), option = el("option", "", name[0] + " on " + name[1]);
            option.value = b.chain + "/" + b.asset;
            select.appendChild(option);
        });
        select.value = current || "SEPOLIA/ETH";
        if (!select.value && select.options.length) select.selectedIndex = 0;
    }

    $id("charge-create").addEventListener("click", async function () {
        var button = this, pair = $id("charge-network").value.split("/");
        $id("charge-error").textContent = "";
        $id("charge-result").hidden = true;
        button.disabled = true;
        try {
            var p = await api("POST", "/payments", { chain: pair[0], asset: pair[1], amount: $id("charge-amount").value.trim() });
            var link = el("a", "", "Open the checkout page");
            link.href = "/pay/" + encodeURIComponent(p.id);
            link.target = "_blank";
            link.rel = "noopener";
            $id("charge-result").replaceChildren("Payment created: " + p.amount + " " + p.asset + " to " + p.address + ". ", link);
            $id("charge-result").hidden = false;
            loadPayments();
        } catch (e) {
            $id("charge-error").textContent = e.message;
        } finally {
            button.disabled = false;
        }
    });

    // --- Send ---------------------------------------------------------------------------------------
    var sendChain = null;

    function showSend(b) {
        sendChain = b.chain;
        $id("send-title").textContent = "Send " + b.asset;
        $id("send-network").textContent = label(b.chain, b.asset)[1];
        $id("send-to").value = "";
        $id("send-amount").value = "";
        $id("send-max").checked = false;
        $id("send-amount").disabled = false;
        $id("send-error").textContent = "";
        $id("send-review").disabled = false;
        $id("receive").hidden = true;
        $id("send").hidden = false;
        $id("send").scrollIntoView({ block: "nearest" });
        $id("send-to").focus();
    }

    function showSigner() {
        $id("forget-box").hidden = true;
        $id("vault").hidden = false;
        $id("vault").scrollIntoView({ block: "start" });
    }

    function hideSigner() {
        $id("vault").hidden = true;
        $id("forget-box").hidden = false;
    }

    $id("send-max").addEventListener("change", function () {
        $id("send-amount").disabled = this.checked;
        if (this.checked) $id("send-amount").value = "";
    });
    $id("send-close").addEventListener("click", function () { $id("send").hidden = true; });

    $id("send-review").addEventListener("click", async function () {
        var to = $id("send-to").value.trim(), amount = $id("send-amount").value.trim(), max = $id("send-max").checked;
        var error = !to ? "Enter the address to send to." : (!max && !/^\d+(\.\d+)?$/.test(amount) ? "Enter an amount, like 0.005." : "");
        $id("send-error").textContent = error;
        if (error) return;
        var button = this;
        button.disabled = true;
        $id("send-result").hidden = true;
        try {
            var prep = await api("GET", "/wallet/send/" + sendChain);
        } catch (err) {
            $id("send-error").textContent = err.message;
            button.disabled = false;
            return;
        }
        showSigner();
        var speed = $id("send-speed").value;
        var request = { id: String(Date.now()), chain: sendChain, to: to, amount: amount, max: max };
        if (prep.kind === "evm") {
            request.fee = prep.fees[speed];
            request.gas_limit = prep.gas_limit;
            request.sources = prep.sources;
        } else {
            request.fee_per_byte = prep.fee_per_byte[speed];
            request.utxos = prep.utxos;
            request.change_index = prep.change_index;
        }
        frame.contentWindow.postMessage({ type: "sign-send", request: request }, "*");
    });

    // The frame asked for the signing engine. It checks every file's SHA-256 itself before running it.
    async function sendEngine(names) {
        var files = {}, transfer = [];
        try {
            for (var i = 0; i < names.length; i++) {
                if (!/^[a-z_.-]+$/.test(names[i])) continue;
                var res = await fetch("vendor/wallet-core/" + names[i]);
                if (!res.ok) throw new Error("HTTP " + res.status);
                files[names[i]] = await res.arrayBuffer();
                transfer.push(files[names[i]]);
            }
        } catch (err) {
            showError("Couldn't download the signing engine (" + err.message + "). Check your connection and try again.");
        }
        frame.contentWindow.postMessage({ type: "engine-files", files: files }, "*", transfer);
    }

    async function broadcastSigned(msg) {
        hideSigner();
        $id("send-review").disabled = false;
        try {
            var r = await api("POST", "/wallet/send/" + msg.chain, { txs: msg.txs });
        } catch (err) {
            $id("send-error").textContent = "Not sent: " + err.message;
            return;
        }
        $id("send").hidden = true;
        var result = $id("send-result");
        result.textContent = (r.error ? "Partly sent: " + r.tx_hashes.length + " of " + msg.txs.length + " transfers went through. " + r.error + " "
                                      : "Sent " + msg.amount + " " + msg.symbol + " to " + msg.to + " (network fee " + msg.fee + " " + msg.symbol + "). ");
        r.tx_hashes.forEach(function (hash, i) {
            var link = el("a", "", r.tx_hashes.length > 1 ? "Transfer " + (i + 1) : "View transaction");
            link.href = EXPLORER[msg.chain] + encodeURIComponent(hash);
            link.rel = "noreferrer";
            link.target = "_blank";
            result.append(link, " ");
        });
        result.hidden = false;
        loadBalances();
    }

    function copy(inputId, button) {
        var input = $id(inputId);
        var done = function () {
            button.textContent = "Copied";
            setTimeout(function () { button.textContent = "Copy"; }, 1500);
        };
        if (navigator.clipboard) navigator.clipboard.writeText(input.value).then(done, function () { input.select(); });
        else input.select();
    }

    $id("refresh").addEventListener("click", loadDashboard);
    $id("receive-close").addEventListener("click", function () { $id("receive").hidden = true; });
    $id("receive-copy").addEventListener("click", function () { copy("receive-address", this); });
    $id("new-key-copy").addEventListener("click", function () { copy("new-key-value", this); });
    $id("new-key").addEventListener("click", async function () {
        // Two clicks: the old key stops working, which breaks the shop until it's updated.
        if (this.dataset.armed !== "1") {
            this.dataset.armed = "1";
            this.textContent = "Click again: replace my API key";
            return;
        }
        this.dataset.armed = "";
        this.textContent = "Make a new API key";
        try {
            var r = await api("POST", "/accounts/me/api-key");
            $id("new-key-value").value = r.api_key;
            $id("new-key-box").hidden = false;
        } catch (e) {
            showError("Couldn't make a new API key (" + e.message + ").");
        }
    });

    $id("lock").addEventListener("click", startFrame);

    $id("forget").addEventListener("click", function () {
        try { localStorage.removeItem(VAULT_KEY); } catch (e) { /* nothing saved, nothing to remove */ }
        frame.src = "about:blank";
        clearTimeout(timer);
        $id("vault").hidden = true;
        $id("none").hidden = false;
    });

    startFrame();
})();
